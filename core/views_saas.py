from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum, Count
from django.db import transaction

from .models import Residencial, SuscripcionResidencial, PlanSuscripcion, Usuario, FacturaSaaS
from .forms import ResidencialOnboardingForm
from .services import AnaliticaSaaSService

def is_superadmin(user):
    return user.is_superuser

@user_passes_test(is_superadmin, login_url='/dashboard/')
def superadmin_dashboard(request):

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

@user_passes_test(is_superadmin, login_url='/dashboard/')
def gestionar_planes(request):
    planes = PlanSuscripcion.objects.all()
    if request.method == 'POST':
        nombre = request.POST.get('nombre')
        precio_por_apartamento = request.POST.get('precio_por_apartamento')
        precio_usuario_extra = request.POST.get('precio_usuario_extra')
        dias_prueba = request.POST.get('dias_prueba_default')
        
        if nombre and precio_por_apartamento:
            PlanSuscripcion.objects.create(
                nombre=nombre,
                precio_por_apartamento=precio_por_apartamento,
                precio_usuario_extra=precio_usuario_extra,
                dias_prueba_default=dias_prueba
            )
            messages.success(request, f"Plan {nombre} creado correctamente.")
            return redirect('gestionar_planes')

    return render(request, 'core/saas/planes.html', {'planes': planes})

@user_passes_test(is_superadmin, login_url='/dashboard/')
def detalle_cliente(request, residencial_id):
    residencial = get_object_or_404(Residencial, id=residencial_id)
    
    if not hasattr(residencial, 'suscripcion'):
        messages.warning(request, "Este residencial no tiene suscripción. Iníciale un Trial primero.")
        return redirect('superadmin_dashboard')
        
    sub = residencial.suscripcion
    mensualidad = sub.calcular_mensualidad()
    facturas = residencial.facturas_saas.order_by('-fecha_emision')
    
    context = {
        'residencial': residencial,
        'suscripcion': sub,
        'mensualidad_estimada': mensualidad,
        'facturas': facturas
    }
    return render(request, 'core/saas/cliente_detalle.html', context)

@user_passes_test(is_superadmin, login_url='/dashboard/')
def cambiar_estado_suscripcion(request, residencial_id, nuevo_estado):
    residencial = get_object_or_404(Residencial, id=residencial_id)
    if hasattr(residencial, 'suscripcion'):
        sub = residencial.suscripcion
        if nuevo_estado in dict(SuscripcionResidencial.ESTADOS).keys():
            sub.estado = nuevo_estado
            sub.save()
            messages.success(request, f"Estado de {residencial.nombre} cambiado a {nuevo_estado}")
    return redirect('detalle_cliente', residencial_id=residencial.id)

@user_passes_test(is_superadmin, login_url='/dashboard/')
def facturacion_b2b(request):
    facturas = FacturaSaaS.objects.select_related('residencial').order_by('-fecha_emision')
    total_pendiente = facturas.filter(estado='PENDIENTE').aggregate(Sum('monto'))['monto__sum'] or 0
    total_recaudado = facturas.filter(estado='PAGADA').aggregate(Sum('monto'))['monto__sum'] or 0
    
    context = {
        'facturas': facturas,
        'total_pendiente': total_pendiente,
        'total_recaudado': total_recaudado
    }
    return render(request, 'core/saas/facturacion.html', context)

@user_passes_test(is_superadmin, login_url='/dashboard/')
def crear_cliente_saas(request):
    if request.method == 'POST':
        form = ResidencialOnboardingForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    # 1. Crear el Residencial
                    residencial = form.save()
                    
                    # 2. Obtener el plan seleccionado
                    plan = form.cleaned_data['plan_suscripcion']
                    
                    # 3. Crear la suscripción automáticamente en Trial
                    fecha_inicio = timezone.now().date()
                    fecha_venc = fecha_inicio + timedelta(days=plan.dias_prueba_default)
                    
                    SuscripcionResidencial.objects.create(
                        residencial=residencial,
                        plan=plan,
                        estado='PRUEBA',
                        fecha_vencimiento_licencia=fecha_venc
                    )
                    
                    messages.success(request, f"¡Residencial {residencial.nombre} creado y activado en Trial por {plan.dias_prueba_default} días!")
                    return redirect('detalle_cliente', residencial_id=residencial.id)
            except Exception as e:
                messages.error(request, f"Error al crear el cliente: {str(e)}")
        else:
            messages.error(request, "Por favor corrige los errores del formulario.")
    else:
        form = ResidencialOnboardingForm()
        
    return render(request, 'core/saas/saas_crear_cliente.html', {'form': form})

@user_passes_test(is_superadmin, login_url='/dashboard/')
def reporte_inteligencia(request):
    ingresos_residenciales = AnaliticaSaaSService.obtener_ingresos_globales_residenciales()
    gastos_residenciales = AnaliticaSaaSService.obtener_gastos_globales()
    rentabilidad_saas = AnaliticaSaaSService.obtener_rentabilidad_saas()
    mrr_estimado = AnaliticaSaaSService.obtener_mr_estimado()

    context = {
        'ingresos_residenciales': ingresos_residenciales,
        'gastos_residenciales': gastos_residenciales,
        'rentabilidad_saas': rentabilidad_saas,
        'mrr_estimado': mrr_estimado
    }
    return render(request, 'core/saas/reporte_inteligencia.html', context)

@login_required
def directorio_global_usuarios(request):
    if request.user.rol != 'SUPERADMIN':
        return redirect('dashboard')
        
    usuarios = Usuario.objects.all().select_related('residencial', 'apartamento').order_by('residencial__nombre', 'rol')
    
    return render(request, 'core/saas/directorio_global.html', {
        'usuarios': usuarios
    })

@login_required
def toggle_modulo_seguridad(request, residencial_id):
    if request.user.rol != 'SUPERADMIN':
        return redirect('dashboard')
        
    residencial = get_object_or_404(Residencial, pk=residencial_id)
    residencial.modulo_seguridad_activo = not residencial.modulo_seguridad_activo
    residencial.save()
    
    estado = "Activado" if residencial.modulo_seguridad_activo else "Desactivado"
    messages.success(request, f"Módulo de Seguridad {estado} para {residencial.nombre}.")
    return redirect('detalle_cliente', residencial_id=residencial.id)

