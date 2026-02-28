from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta, datetime
# IMPORTANTE: Agregamos Usuario a esta lista y quitamos la importación de 'auth.User'
from .models import Reserva, AreaSocial, BloqueoFecha, LecturaGas, Apartamento, Gasto, Aviso, Usuario, Incidencia, ReportePago

# ==========================================
# 1. FORMULARIO DE RESERVAS
# ==========================================
class ReservaForm(forms.ModelForm):
    class Meta:
        model = Reserva
        fields = ['area_social', 'fecha_solicitud', 'hora_inicio', 'hora_fin']
        
        widgets = {
            'fecha_solicitud': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Selecciona fecha...'}),
            'area_social': forms.Select(attrs={'class': 'form-select'}),
            'hora_inicio': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'hora_fin': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
        }
        labels = {
            'area_social': '¿Qué área deseas reservar?',
            'fecha_solicitud': '¿Para cuándo?',
            'hora_inicio': 'Hora Inicio',
            'hora_fin': 'Hora Fin'
        }

    def __init__(self, user, *args, **kwargs):
        self.user = user 
        super().__init__(*args, **kwargs)
        if user.residencial:
            self.fields['area_social'].queryset = AreaSocial.objects.filter(residencial=user.residencial)

    def clean(self):
        cleaned_data = super().clean()
        fecha = cleaned_data.get('fecha_solicitud')
        inicio = cleaned_data.get('hora_inicio')
        fin = cleaned_data.get('hora_fin')
        residencial = self.user.residencial

        if not (fecha and inicio and fin and residencial):
            return 

        # 1. VALIDAR SI EL DÍA ESTÁ BLOQUEADO POR EL ADMIN
        bloqueo = BloqueoFecha.objects.filter(residencial=residencial, fecha=fecha).first()
        if bloqueo:
            raise ValidationError(f"⛔ No se pueden hacer reservas este día. Motivo: {bloqueo.motivo}")

        # 2. VALIDAR ANTICIPACIÓN
        hoy = timezone.now().date()
        dias_diferencia = (fecha - hoy).days

        if dias_diferencia < residencial.dias_minimos_anticipacion:
            raise ValidationError(f"Debes reservar con al menos {residencial.dias_minimos_anticipacion} días de anticipación.")
        
        if dias_diferencia > residencial.dias_maximos_anticipacion:
            raise ValidationError(f"No puedes reservar con más de {residencial.dias_maximos_anticipacion} días de adelanto.")

        # 3. VALIDAR HORARIO
        dummy_date = datetime.now().date()
        dt_inicio = datetime.combine(dummy_date, inicio)
        dt_fin = datetime.combine(dummy_date, fin)

        if dt_fin <= dt_inicio:
            raise ValidationError("La hora de fin debe ser después de la hora de inicio.")

        duracion = (dt_fin - dt_inicio).total_seconds() / 3600 
        
        if duracion > residencial.duracion_maxima_horas:
            raise ValidationError(f"La duración máxima permitida es de {residencial.duracion_maxima_horas} horas. Estás solicitando {duracion:.1f} horas.")

        return cleaned_data

# ==========================================
# 2. FORMULARIO DE FACTURACIÓN DE GAS (MODIFICADO)
# ==========================================
class LecturaGasForm(forms.ModelForm):
    class Meta:
        model = LecturaGas
        # Incluimos TODOS los campos para que no desaparezcan del HTML
        fields = ['apartamento', 'lectura_anterior', 'lectura_actual', 'precio_galon_mes', 'fecha_lectura']
        
        widgets = {
            'apartamento': forms.Select(attrs={'class': 'form-select'}),
            'fecha_lectura': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            
            # NO EDITABLE (Solo lectura)
            'lectura_anterior': forms.NumberInput(attrs={'class': 'form-control', 'readonly': 'readonly'}),
            
            # AQUÍ EL CAMBIO PARA LOS 3 DECIMALES
            'lectura_actual': forms.NumberInput(attrs={
                'class': 'form-control', 
                'step': '0.001',   # <--- Permite 3 decimales (123.456)
                'placeholder': '0.000'
            }),
            
            'precio_galon_mes': forms.NumberInput(attrs={
                'class': 'form-control', 
                'step': '0.01', 
                'placeholder': '0.00'
            }),
        }
        labels = {
            'lectura_anterior': 'Lectura Anterior (m3)',
            'lectura_actual': 'Lectura Actual (m3)',
            'precio_galon_mes': 'Precio Compra Galón ($)',
            'fecha_lectura': 'Fecha de Corte'
        }

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if user.residencial:
            self.fields['apartamento'].queryset = Apartamento.objects.filter(residencial=user.residencial).order_by('numero')
            
            # Pre-llenar precio (Opcional)
            ultimo = LecturaGas.objects.filter(residencial=user.residencial).last()
            if ultimo:
                self.fields['precio_galon_mes'].initial = ultimo.precio_galon_mes

# ==========================================
# 3. FORMULARIO DE GASTOS
# ==========================================
class GastoForm(forms.ModelForm):
    class Meta:
        model = Gasto
        fields = ['descripcion', 'monto', 'fecha_gasto', 'categoria']
        
        widgets = {
            'descripcion': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: Pago Luz Área Común'}),
            'monto': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0.00'}),
            'fecha_gasto': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'categoria': forms.Select(attrs={'class': 'form-select'}),
        }
        labels = {
            'descripcion': 'Descripción del Gasto',
            'fecha_gasto': 'Fecha de Factura',
        }

# ==========================================
# 4. FORMULARIO DE AVISOS
# ==========================================
class AvisoForm(forms.ModelForm):
    class Meta:
        model = Aviso
        fields = ['titulo', 'mensaje']
        
        widgets = {
            'titulo': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: Mantenimiento de Elevador'}),
            'mensaje': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Escribe aquí los detalles...'}),
        }

# ==========================================
# 5. GESTIÓN DE USUARIOS
# ==========================================
class RegistroVecinoForm(forms.ModelForm):
    # Campo extra para seleccionar apartamento
    apartamento = forms.ModelChoiceField(
        queryset=None, 
        required=False, 
        label="Asignar Apartamento",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = Usuario 
        fields = ['username', 'first_name', 'last_name', 'email', 'password', 'telefono'] 
        
        widgets = {
            'password': forms.PasswordInput(attrs={'class': 'form-control'}),
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'telefono': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: 809-555-5555'}),
        }

    def __init__(self, admin_user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if admin_user.residencial:
            self.fields['apartamento'].queryset = Apartamento.objects.filter(residencial=admin_user.residencial)

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"]) 
        if commit:
            user.save()
        return user


class IncidenciaForm(forms.ModelForm):
    class Meta:
        model = Incidencia
        fields = ['titulo', 'descripcion', 'foto']
        widgets = {
            'titulo': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: Bombillo quemado en pasillo'}),
            'descripcion': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Detalles del problema...'}),
            'foto': forms.FileInput(attrs={'class': 'form-control'}),
        }

class EditarVecinoForm(forms.ModelForm):
    class Meta:
        model = Usuario
        fields = ['first_name', 'last_name', 'email', 'telefono', 'apartamento']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'telefono': forms.TextInput(attrs={'class': 'form-control'}),
            'apartamento': forms.Select(attrs={'class': 'form-select'}),
        }


class AbonoForm(forms.Form):
    usuario = forms.ModelChoiceField(
        queryset=Usuario.objects.none(), 
        label="Vecino",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    monto = forms.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        label="Monto a Abonar",
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0.00'})
    )
    concepto = forms.CharField(
        max_length=100, 
        label="Concepto / Nota",
        initial="Pago Adelantado",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: Adelanto Marzo 2026'})
    )

    def __init__(self, admin_user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if admin_user:
            self.fields['usuario'].queryset = Usuario.objects.filter(
                residencial=admin_user.residencial,
                rol='RESIDENTE'
            ).order_by('apartamento__numero')

class ReportePagoForm(forms.ModelForm):
    class Meta:
        model = ReportePago
        fields = ['monto', 'tipo_pago', 'comprobante', 'nota_usuario']
        widgets = {
            'monto': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0.00'}),
            'tipo_pago': forms.Select(attrs={'class': 'form-select'}),
            'nota_usuario': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Ej: Transferencia Banco Popular...'}),
            'comprobante': forms.FileInput(attrs={'class': 'form-control'}),
        }
        labels = {
            'tipo_pago': 'Concepto del Pago',
            'monto': 'Monto Pagado',
            'comprobante': 'Foto del Comprobante'
        }