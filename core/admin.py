from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
# 1. ACTUALIZAMOS IMPORTS: Agregamos Incidencia
from .models import (
    Usuario, Residencial, Apartamento, AreaSocial, 
    Reserva, BloqueoFecha, Gasto, Factura, LecturaGas, Aviso, Incidencia, ReportePago, Aviso
)

# --- CONFIGURACIÓN DE USUARIO ---
class CustomUserAdmin(UserAdmin):
    model = Usuario
    # AGREGADO: 'saldo_a_favor' para verlo en la lista
    list_display = ['username', 'first_name', 'last_name', 'rol', 'apartamento', 'saldo_favor_mantenimiento', 'saldo_favor_gas', 'residencial']
    
    # AGREGADO: 'saldo_a_favor' en fieldsets para poder EDITARLO manualmente
    fieldsets = UserAdmin.fieldsets + (
        ('Información Residencial', {'fields': ('rol', 'telefono', 'residencial', 'apartamento', 'saldo_favor_mantenimiento', 'saldo_favor_gas')}),
    )
    
    add_fieldsets = UserAdmin.add_fieldsets + (
        (None, {'fields': ('rol', 'telefono', 'residencial', 'apartamento', 'saldo_favor_mantenimiento', 'saldo_favor_gas')}),
    )

# --- CONFIGURACIÓN DE RESIDENCIAL ---
class ApartamentoInline(admin.TabularInline):
    model = Apartamento
    extra = 1

class ResidencialAdmin(admin.ModelAdmin):
    inlines = [ApartamentoInline]
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
# SECCIONES DE FINANZAS
# =========================================================

@admin.register(Gasto)
class GastoAdmin(admin.ModelAdmin):
    list_display = ('descripcion', 'monto', 'categoria', 'fecha_gasto', 'residencial')
    list_filter = ('residencial', 'categoria', 'fecha_gasto')

@admin.register(Factura)
class FacturaAdmin(admin.ModelAdmin):
    # AGREGADO: saldo_pendiente y monto_pagado para monitorear abonos
    list_display = ('concepto', 'usuario', 'tipo', 'monto', 'saldo_pendiente', 'estado', 'residencial')
    list_filter = ('estado', 'tipo', 'residencial')
    search_fields = ('usuario__username', 'concepto')

@admin.register(LecturaGas)
class LecturaGasAdmin(admin.ModelAdmin):
    list_display = ('apartamento', 'lectura_anterior', 'lectura_actual', 'consumo_galones', 'total_a_pagar', 'fecha_lectura')
    list_filter = ('residencial', 'fecha_lectura')
    readonly_fields = ('consumo_galones', 'total_a_pagar', 'factura_generada')

# =========================================================
# GESTIÓN DE INCIDENCIAS Y AVISOS
# =========================================================

@admin.register(Aviso)
class AvisoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'residencial', 'fecha_creacion')
    list_filter = ('residencial',)

@admin.register(Incidencia)
class IncidenciaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'usuario', 'estado', 'fecha_creacion', 'residencial')
    list_filter = ('estado', 'residencial')
    search_fields = ('titulo', 'usuario__username')

# --- REGISTRO DE MODELOS RESTANTES ---
admin.site.register(Usuario, CustomUserAdmin)
admin.site.register(Residencial, ResidencialAdmin)
admin.site.register(Apartamento)
admin.site.register(AreaSocial)
admin.site.register(Reserva, ReservaAdmin)
admin.site.register(BloqueoFecha, BloqueoFechaAdmin)
admin.site.register(ReportePago)
