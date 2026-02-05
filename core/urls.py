from django.contrib import admin
from django.urls import path, include
from . import views


urlpatterns = [
    # Dashboard principal
    path('', views.dashboard, name='dashboard'),
    
    # Crear reserva (Residente)
    path('reservar/', views.crear_reserva, name='crear_reserva'),
    
    # Gestionar reserva (Admin - Aprobar/Rechazar)
    path('gestionar-reserva/<int:reserva_id>/<str:accion>/', views.gestionar_reserva, name='gestionar_reserva'),
    
    # Cancelar reserva (Residente - Borrar)
    path('cancelar-reserva/<int:reserva_id>/', views.cancelar_reserva, name='cancelar_reserva'),

    # API Calendario (Datos JSON)
    path('api/eventos/', views.obtener_eventos_calendario, name='api_eventos'),

    # --- ESTA ES LA QUE TE FALTABA (Bloquear Fecha) ---
    path('bloquear-fecha/', views.bloquear_fecha, name='bloquear_fecha'),

    path('facturacion/gas/', views.registrar_lectura_gas, name='registrar_lectura_gas'),

    path('facturacion/generar-cuotas/', views.generar_cuotas_masivas, name='generar_cuotas_masivas'),

    # ... tus otras rutas ...
    path('finanzas/cobros/', views.cuentas_por_cobrar, name='cuentas_por_cobrar'),

    path('finanzas/pagar/<int:factura_id>/', views.registrar_pago, name='registrar_pago'),

    path('finanzas/gastos/nuevo/', views.registrar_gasto, name='registrar_gasto'),

    path('finanzas/reporte/', views.reporte_financiero, name='reporte_financiero'),

    path('avisos/nuevo/', views.crear_aviso, name='crear_aviso'),

    path('avisos/borrar/<int:aviso_id>/', views.borrar_aviso, name='borrar_aviso'),

    path('finanzas/recibo/<int:factura_id>/', views.ver_recibo, name='ver_recibo'),

    path('vecinos/', views.lista_vecinos, name='lista_vecinos'),

    path('vecinos/nuevo/', views.crear_vecino, name='crear_vecino'),

    path('incidencias/nueva/', views.crear_incidencia, name='crear_incidencia'),
    
    path('incidencias/gestion/', views.gestionar_incidencias, name='gestionar_incidencias'),

    path('vecinos/editar/<int:user_id>/', views.editar_vecino, name='editar_vecino'),
    
    path('vecinos/clave/<int:user_id>/', views.cambiar_clave_vecino, name='cambiar_clave_vecino'),
]