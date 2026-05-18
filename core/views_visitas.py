from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone

from .models import Visita, Reserva
from .forms import VisitaForm

@login_required
def mis_visitas(request):
    if request.user.rol not in ['RESIDENTE', 'ADMIN_RESIDENCIAL']:
        return redirect('dashboard')
        
    if not request.user.apartamento:
        messages.warning(request, "Necesitas tener un apartamento asignado para registrar visitas.")
        return redirect('dashboard')
        
    visitas = Visita.objects.filter(
        residente=request.user, 
        apartamento=request.user.apartamento
    ).order_by('-fecha_esperada', '-fecha_registro')
    
    if request.method == 'POST':
        form = VisitaForm(request.POST)
        if form.is_valid():
            nueva_visita = form.save(commit=False)
            nueva_visita.residencial = request.user.residencial
            nueva_visita.apartamento = request.user.apartamento
            nueva_visita.residente = request.user
            nueva_visita.save()
            messages.success(request, f"Visita de {nueva_visita.nombre_visitante} programada correctamente.")
            return redirect('mis_visitas')
    else:
        form = VisitaForm()
        
    return render(request, 'core/visitas/mis_visitas.html', {
        'visitas': visitas,
        'form': form
    })

@login_required
def cancelar_visita(request, visita_id):
    visita = get_object_or_404(Visita, pk=visita_id, residente=request.user)
    
    if visita.estado == 'ESPERADA':
        visita.estado = 'CANCELADA'
        visita.save()
        messages.success(request, "Visita cancelada.")
    else:
        messages.error(request, "No puedes cancelar una visita que ya ingresó o finalizó.")
        
    return redirect('mis_visitas')

@login_required
def gestionar_invitados_reserva(request, reserva_id):
    if request.user.rol not in ['RESIDENTE', 'ADMIN_RESIDENCIAL']:
        return redirect('dashboard')
        
    reserva = get_object_or_404(Reserva, pk=reserva_id, usuario=request.user)
    
    if reserva.estado != 'APROBADA':
        messages.error(request, "Solo puedes subir invitados a reservas aprobadas.")
        return redirect('dashboard')
        
    if request.method == 'POST':
        nombres = request.POST.get('lista_nombres', '')
        nombres_lista = [n.strip() for n in nombres.split('\n') if n.strip()]
        
        # Eliminar las visitas (invitados) previos de esta reserva y volver a crearlos
        # (Así funciona como una "actualización masiva")
        Visita.objects.filter(reserva_asociada=reserva).delete()
        
        nuevas_visitas = []
        for nombre in nombres_lista:
            nuevas_visitas.append(
                Visita(
                    residencial=request.user.residencial,
                    apartamento=request.user.apartamento,
                    residente=request.user,
                    nombre_visitante=nombre[:150], # limitar largo
                    fecha_esperada=reserva.fecha_solicitud,
                    estado='ESPERADA',
                    reserva_asociada=reserva
                )
            )
            
        if nuevas_visitas:
            Visita.objects.bulk_create(nuevas_visitas)
            
        messages.success(request, f"Se han registrado {len(nuevas_visitas)} invitados para tu evento.")
        return redirect('dashboard')
        
    invitados_actuales = Visita.objects.filter(reserva_asociada=reserva).values_list('nombre_visitante', flat=True)
    texto_actual = "\n".join(invitados_actuales)
    
    return render(request, 'core/visitas/gestionar_invitados.html', {
        'reserva': reserva,
        'texto_actual': texto_actual
    })

# ========================================================
# VISTAS DEL PERSONAL DE SEGURIDAD (GARITA VIRTUAL)
# ========================================================

@login_required
def dashboard_seguridad(request):
    if request.user.rol != 'SEGURIDAD':
        return redirect('dashboard')
        
    hoy = timezone.now().date()
    residencial = request.user.residencial
    
    # Visitas del día
    visitas_hoy = Visita.objects.filter(
        residencial=residencial,
        fecha_esperada=hoy
    ).exclude(estado='CANCELADA').order_by('apartamento__numero', 'fecha_registro')
    
    # Reservas (Eventos) de hoy
    reservas_hoy = Reserva.objects.filter(
        residencial=residencial,
        fecha_solicitud=hoy,
        estado='APROBADA'
    ).order_by('hora_inicio')

    return render(request, 'core/visitas/dashboard_seguridad.html', {
        'visitas_hoy': visitas_hoy,
        'reservas_hoy': reservas_hoy,
        'hoy': hoy
    })

@login_required
def marcar_entrada_visita(request, visita_id):
    if request.user.rol != 'SEGURIDAD':
        return redirect('dashboard')
        
    visita = get_object_or_404(Visita, pk=visita_id, residencial=request.user.residencial)
    if visita.estado == 'ESPERADA':
        visita.estado = 'EN_CURSO'
        visita.hora_entrada = timezone.now()
        visita.save()
        messages.success(request, f"Entrada registrada para {visita.nombre_visitante}.")
        
    return redirect('dashboard_seguridad')

@login_required
def marcar_salida_visita(request, visita_id):
    if request.user.rol != 'SEGURIDAD':
        return redirect('dashboard')
        
    visita = get_object_or_404(Visita, pk=visita_id, residencial=request.user.residencial)
    if visita.estado == 'EN_CURSO':
        visita.estado = 'FINALIZADA'
        visita.hora_salida = timezone.now()
        visita.save()
        messages.success(request, f"Salida registrada para {visita.nombre_visitante}.")
        
    return redirect('dashboard_seguridad')
