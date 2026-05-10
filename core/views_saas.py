from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum, Count

from .models import Residencial, SuscripcionResidencial, PlanSuscripcion, Usuario

@login_required
def superadmin_dashboard(request):
    # Seguridad: Solo accesible por Super Administradores de la Plataforma
    if request.user.rol != 'SUPERADMIN':
        messages.error(request, "Acceso restringido al área SaaS.")
        return redirect('dashboard')

    # 1. KPIs (Métricas principales)
    suscripciones = SuscripcionResidencial.objects.select_related('residencial', 'plan').prefetch_related('servicios_adicionales')
    
    clientes_activos = suscripciones.filter(estado='ACTIVA').count()
    clientes_prueba = suscripciones.filter(estado='PRUEBA').count()
    clientes_suspendidos = suscripciones.filter(estado='SUSPENDIDA').count()
    
    # MRR (Monthly Recurring Revenue) Estimado
    mrr_estimado = sum(sub.calcular_mensualidad() for sub in suscripciones.filter(estado__in=['ACTIVA', 'PRUEBA']))

    # 2. Tabla de Residenciales (Armar la data)
    datos_clientes = []
    for sub in suscripciones:
        datos_clientes.append({
            'suscripcion': sub,
            'residencial': sub.residencial,
            'plan': sub.plan,
            'apartamentos': sub.residencial.apartamentos.count(),
            'usuarios_extra': max(0, sub.residencial.usuario_set.filter(rol__in=['ADMIN_RESIDENCIAL', 'ASISTENTE']).count() - 2),
            'mensualidad': sub.calcular_mensualidad(),
            'dias_restantes': (sub.fecha_vencimiento_licencia - timezone.now().date()).days
        })

    # 3. Residenciales huérfanos (Aún sin suscripción)
    residenciales_sin_suscripcion = Residencial.objects.filter(suscripcion__isnull=True)
    
    # 4. Planes disponibles
    planes = PlanSuscripcion.objects.all()

    context = {
        'clientes_activos': clientes_activos,
        'clientes_prueba': clientes_prueba,
        'clientes_suspendidos': clientes_suspendidos,
        'mrr_estimado': mrr_estimado,
        'datos_clientes': datos_clientes,
        'residenciales_sin_suscripcion': residenciales_sin_suscripcion,
        'planes': planes
    }
    return render(request, 'core/saas/superadmin_dashboard.html', context)

@login_required
def iniciar_trial_saas(request, residencial_id):
    """Asigna 30 días de prueba gratuitos a un nuevo residencial"""
    if request.user.rol != 'SUPERADMIN':
        return redirect('dashboard')
        
    residencial = get_object_or_404(Residencial, id=residencial_id)
    plan_base = PlanSuscripcion.objects.filter(activo=True).first() # Asume un plan por defecto
    
    if not plan_base:
        messages.error(request, "No hay ningún Plan de Suscripción activo creado. Crea uno primero.")
        return redirect('superadmin_dashboard')
        
    if not hasattr(residencial, 'suscripcion'):
        SuscripcionResidencial.objects.create(
            residencial=residencial,
            plan=plan_base,
            estado='PRUEBA',
            fecha_vencimiento_licencia=timezone.now().date() + timedelta(days=plan_base.dias_prueba_default)
        )
        messages.success(request, f"Trial de {plan_base.dias_prueba_default} días iniciado para {residencial.nombre}")
    else:
        messages.warning(request, f"El residencial {residencial.nombre} ya tiene una suscripción.")
        
    return redirect('superadmin_dashboard')
