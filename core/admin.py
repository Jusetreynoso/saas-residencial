from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
# 1. ACTUALIZAMOS IMPORTS: Agregamos Gasto, Factura, LecturaGas
from .models import (
    Usuario, Residencial, Apartamento, AreaSocial, 
    Reserva, BloqueoFecha, Gasto, Factura, LecturaGas, Aviso
)

# --- CONFIGURACIÓN DE USUARIO (Mantenemos la tuya intacta) ---
class CustomUserAdmin(UserAdmin):
    model = Usuario
    list_display = ['username', 'email', 'rol', 'telefono', 'residencial', 'apartamento', 'is_staff']
    fieldsets = UserAdmin.fieldsets + (
        (None, {'fields': ('rol', 'telefono', 'residencial', 'apartamento')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (None, {'fields': ('rol', 'telefono', 'residencial', 'apartamento')}),
    )

# --- CONFIGURACIÓN DE RESIDENCIAL ---
class ApartamentoInline(admin.TabularInline):
    model = Apartamento
    extra = 1

class ResidencialAdmin(admin.ModelAdmin):
    inlines = [ApartamentoInline]
    # Mantenemos las columnas de reglas que agregamos antes
    list_display = ['nombre', 'direccion', 'permite_reservas', 'dias_minimos_anticipacion', 'duracion_maxima_horas']

# --- CONFIGURACIÓN DE RESERVAS ---
class ReservaAdmin(admin.ModelAdmin):
    list_display = ['residencial', 'area_social', 'usuario', 'fecha_solicitud', 'hora_inicio', 'hora_fin', 'estado']
    list_filter = ['estado', 'residencial', 'fecha_solicitud']

# --- CONFIGURACIÓN DE BLOQUEOS DE FECHA ---
class BloqueoFechaAdmin(admin.ModelAdmin):
    list_display = ('fecha', 'motivo', 'residencial')
    list_filter = ('residencial',)

# =========================================================
# NUEVAS SECCIONES DE FINANZAS (AGREGADAS AHORA)
# =========================================================

@admin.register(Gasto)
class GastoAdmin(admin.ModelAdmin):
    list_display = ('descripcion', 'monto', 'categoria', 'fecha_gasto', 'residencial')
    list_filter = ('residencial', 'categoria', 'fecha_gasto')

@admin.register(Factura)
class FacturaAdmin(admin.ModelAdmin):
    list_display = ('concepto', 'usuario', 'tipo', 'monto', 'estado', 'residencial')
    list_filter = ('estado', 'tipo', 'residencial')
    search_fields = ('usuario__username', 'concepto')

@admin.register(LecturaGas)
class LecturaGasAdmin(admin.ModelAdmin):
    # Mostramos los campos calculados para verificar que la fórmula funciona
    list_display = ('apartamento', 'lectura_anterior', 'lectura_actual', 'consumo_galones', 'total_a_pagar', 'fecha_lectura')
    list_filter = ('residencial', 'fecha_lectura')
    # Hacemos que los campos calculados sean solo lectura (para que nadie los falsee)
    readonly_fields = ('consumo_galones', 'total_a_pagar', 'factura_generada')

# =========================================================

# --- REGISTRO DE MODELOS ---
admin.site.register(Usuario, CustomUserAdmin)
admin.site.register(Residencial, ResidencialAdmin)
admin.site.register(Apartamento)
admin.site.register(AreaSocial)
admin.site.register(Reserva, ReservaAdmin)
admin.site.register(BloqueoFecha, BloqueoFechaAdmin)
# NOTA: Gasto, Factura y LecturaGas ya se registraron arriba con el decorador @admin.register

@admin.register(Aviso)
class AvisoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'residencial', 'fecha_creacion')
    list_filter = ('residencial',)