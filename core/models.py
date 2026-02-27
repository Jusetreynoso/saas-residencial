from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal # <--- IMPORTANTE: Necesario para cálculos financieros

# ---------------------------------------------------------
# 1. Nivel Jerárquico (Multi-tenancy)
# ---------------------------------------------------------

class Residencial(models.Model):
    nombre = models.CharField(max_length=100)
    direccion = models.TextField()
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    permite_reservas = models.BooleanField(default=True)

    # Reglas configurables
    dias_minimos_anticipacion = models.IntegerField(default=7, help_text="Días mínimos antes de la reserva")
    dias_maximos_anticipacion = models.IntegerField(default=30, help_text="Días máximos de futuro permitidos")
    duracion_maxima_horas = models.IntegerField(default=5, help_text="Horas máximas permitidas por evento")

    # --- NUEVO: CONFIGURACIÓN FINANCIERA ---
    # Día del mes que se genera la factura (ej: día 1)
    dia_corte = models.IntegerField(default=1, help_text="Día del mes para generar cuotas")
    # Cuántos días tienen para pagar antes de mora (ej: 15 días)
    dias_gracia = models.IntegerField(default=15, help_text="Días después del corte para pagar sin mora")
    # Porcentaje de recargo (ej: 5.00%)
    porcentaje_mora = models.DecimalField(max_digits=5, decimal_places=2, default=5.00, help_text="% de Recargo por mora")

    saldo_inicial = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, help_text="Dinero en banco antes de usar el sistema")

    def __str__(self):
        return self.nombre

class Apartamento(models.Model):
    residencial = models.ForeignKey(Residencial, on_delete=models.CASCADE, related_name='apartamentos')
    numero = models.CharField(max_length=10, help_text="Ej: C-102")
    piso = models.CharField(max_length=10, blank=True)

    # --- NUEVO: CUOTA PERSONALIZADA POR APARTAMENTO ---
    # Cada apto puede pagar diferente (por metros cuadrados o reglamento)
    monto_cuota = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Cuota mensual de mantenimiento")
    
    class Meta:
        unique_together = ('residencial', 'numero') 

    def __str__(self):
        return f"{self.numero} - {self.residencial.nombre}"

# ---------------------------------------------------------
# 2. Usuario Personalizado
# ---------------------------------------------------------

class Usuario(AbstractUser):
    ROLES = (
        ('SUPERADMIN', 'Super Administrador (Todo el sistema)'),
        ('ADMIN_RESIDENCIAL', 'Administrador de Residencial'),
        ('RESIDENTE', 'Residente'),
    )
    
    rol = models.CharField(max_length=20, choices=ROLES, default='RESIDENTE')
    telefono = models.CharField(max_length=20, blank=True, null=True, help_text="+1809xxxxxxx")
    
    residencial = models.ForeignKey(Residencial, on_delete=models.CASCADE, null=True, blank=True)
    apartamento = models.ForeignKey(Apartamento, on_delete=models.SET_NULL, null=True, blank=True, related_name='habitantes')

    saldo_a_favor = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        return f"{self.username} ({self.get_rol_display()})"

# ---------------------------------------------------------
# 3. Nivel Social (Reservas)
# ---------------------------------------------------------

class AreaSocial(models.Model):
    residencial = models.ForeignKey(Residencial, on_delete=models.CASCADE)
    nombre = models.CharField(max_length=50, help_text="Ej: Gazebo, Piscina")
    capacidad = models.IntegerField(default=10)

    def __str__(self):
        return f"{self.nombre} ({self.residencial.nombre})"

class Reserva(models.Model):
    ESTADOS = (
        ('PENDIENTE', 'Pendiente'),
        ('APROBADA', 'Aprobada'),
        ('RECHAZADA', 'Rechazada'),
    )

    residencial = models.ForeignKey(Residencial, on_delete=models.CASCADE)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE)
    area_social = models.ForeignKey(AreaSocial, on_delete=models.CASCADE)
    
    fecha_solicitud = models.DateField()
    hora_inicio = models.TimeField(null=True) 
    hora_fin = models.TimeField(null=True)    

    fecha_creacion = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default='PENDIENTE')
    motivo_rechazo = models.TextField(blank=True, null=True)

    def clean(self):
        try:
            self.usuario
        except:
            return 

        # 1. Una reserva al mes por apto
        if self.usuario.apartamento:
            mes = self.fecha_solicitud.month
            year = self.fecha_solicitud.year
            
            reservas_mes = Reserva.objects.filter(
                residencial=self.residencial,
                usuario__apartamento=self.usuario.apartamento,
                fecha_solicitud__month=mes,
                fecha_solicitud__year=year
            ).exclude(pk=self.pk).exclude(estado='RECHAZADA')

            if reservas_mes.exists():
                raise ValidationError("Tu apartamento ya tiene una solicitud activa para este mes.")

        # 2. Área ocupada
        coincidencias = Reserva.objects.filter(
            residencial=self.residencial,
            area_social=self.area_social,
            fecha_solicitud=self.fecha_solicitud
        ).exclude(pk=self.pk).exclude(estado='RECHAZADA')

        if coincidencias.exists():
            raise ValidationError(f"El área {self.area_social.nombre} ya está reservada para esa fecha.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Reserva {self.area_social} - {self.fecha_solicitud}"

# ---------------------------------------------------------
# 4. Bloqueos de Calendario
# ---------------------------------------------------------
class BloqueoFecha(models.Model):
    residencial = models.ForeignKey(Residencial, on_delete=models.CASCADE)
    fecha = models.DateField()
    motivo = models.CharField(max_length=100, help_text="Ej: Mantenimiento Piscina")
    
    class Meta:
        unique_together = ('residencial', 'fecha')

    def __str__(self):
        return f"Bloqueo: {self.fecha} - {self.motivo}"

# ---------------------------------------------------------
# 5. Módulo de Finanzas (GASTOS, FACTURAS, GAS)
# ---------------------------------------------------------

class Gasto(models.Model):
    CATEGORIAS = (
        ('GAS', 'Compra de Gas (Camión)'),
        ('SERVICIOS', 'Servicios Básicos (Luz, Agua)'),
        ('MANTENIMIENTO', 'Mantenimiento y Reparaciones'),
        ('NOMINA', 'Nómina y Personal'),
        ('OTRO', 'Otros'),
    )

    residencial = models.ForeignKey(Residencial, on_delete=models.CASCADE)
    descripcion = models.CharField(max_length=200, help_text="Ej: Carga de Gas Enero")
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_gasto = models.DateField()
    categoria = models.CharField(max_length=20, choices=CATEGORIAS, default='OTRO')

    def __str__(self):
        return f"{self.descripcion} - ${self.monto}"

class Factura(models.Model):
    TIPOS = (
        ('CUOTA', 'Cuota de Mantenimiento'),
        ('GAS', 'Consumo de Gas'),
        ('EXTRA', 'Cuota Extraordinaria'),
    )
    ESTADOS_PAGO = (
        ('PENDIENTE', 'Pendiente'),
        ('PARCIAL', 'Abono Parcial'),
        ('PAGADO', 'Pagado'),
        ('VENCIDO', 'Vencido'),
    )

    residencial = models.ForeignKey(Residencial, on_delete=models.CASCADE)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='facturas')
    
    tipo = models.CharField(max_length=10, choices=TIPOS, default='CUOTA')
    concepto = models.CharField(max_length=100)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    
    fecha_emision = models.DateField(default=timezone.now)
    fecha_vencimiento = models.DateField()
    fecha_pago = models.DateField(null=True, blank=True)
    
    estado = models.CharField(max_length=10, choices=ESTADOS_PAGO, default='PENDIENTE')

    # --- CAMPOS AGREGADOS PARA PAGOS PARCIALES ---
    monto_pagado = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    saldo_pendiente = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fecha_ultima_mora = models.DateField(null=True, blank=True, help_text="Fecha de la última aplicación de mora")
    # ---------------------------------------------

    def __str__(self):
        return f"{self.concepto} - {self.usuario.username} (${self.monto})"

class LecturaGas(models.Model):
    residencial = models.ForeignKey(Residencial, on_delete=models.CASCADE)
    apartamento = models.ForeignKey(Apartamento, on_delete=models.CASCADE)
    
    fecha_lectura = models.DateField(default=timezone.now)
    
    lectura_anterior = models.DecimalField(max_digits=10, decimal_places=3)
    lectura_actual = models.DecimalField(max_digits=10, decimal_places=3)
    precio_galon_mes = models.DecimalField(max_digits=6, decimal_places=2)
    
    # Default decimal para evitar conflictos con float
    factor_conversion = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('1.20'))
    
    consumo_galones = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    total_a_pagar = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    
    factura_generada = models.ForeignKey(Factura, on_delete=models.SET_NULL, null=True, blank=True)

    def save(self, *args, **kwargs):
        # 1. Consumo m3
        consumo_m3 = self.lectura_actual - self.lectura_anterior
        if consumo_m3 < 0:
            consumo_m3 = Decimal('0.00')
        
        # 2. Convertir factor a Decimal para cálculo seguro
        factor_seguro = Decimal(str(self.factor_conversion))
        
        self.consumo_galones = consumo_m3 * factor_seguro
        
        # 3. Calculamos dinero
        self.total_a_pagar = self.consumo_galones * self.precio_galon_mes
        
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Lectura {self.apartamento} - {self.fecha_lectura}"
    

class Aviso(models.Model):
    residencial = models.ForeignKey(Residencial, on_delete=models.CASCADE)
    titulo = models.CharField(max_length=100, help_text="Título del anuncio")
    mensaje = models.TextField(help_text="Detalle de la noticia")
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.titulo} - {self.fecha_creacion.date()}"

class Incidencia(models.Model):
    ESTADOS = [
        ('PENDIENTE', '🔴 Pendiente'),
        ('EN_PROCESO', '🟠 En Proceso'),
        ('RESUELTO', '🟢 Resuelto'),
        ('RECHAZADO', '⚪ Rechazado'),
    ]
    
    residencial = models.ForeignKey(Residencial, on_delete=models.CASCADE)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE)
    titulo = models.CharField(max_length=100)
    descripcion = models.TextField()
    foto = models.ImageField(upload_to='incidencias/', blank=True, null=True) 
    estado = models.CharField(max_length=20, choices=ESTADOS, default='PENDIENTE')
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    comentario_admin = models.TextField(blank=True, null=True, help_text="Respuesta de la administración")

    def __str__(self):
        return f"{self.titulo} - {self.usuario.username}"
    
class ReportePago(models.Model):
    ESTADOS = [
        ('PENDIENTE', 'Pendiente de Revisión'),
        ('APROBADO', 'Aprobado'),
        ('RECHAZADO', 'Rechazado'),
    ]

    residencial = models.ForeignKey(Residencial, on_delete=models.CASCADE)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE)
    fecha_reporte = models.DateTimeField(auto_now_add=True)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    comprobante = models.ImageField(upload_to='comprobantes/', blank=True, null=True)
    nota_usuario = models.TextField(blank=True, null=True, help_text="Ej: Pago de Marzo y Abril")
    estado = models.CharField(max_length=20, choices=ESTADOS, default='PENDIENTE')
    comentario_admin = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Pago ${self.monto} - {self.usuario.username}"