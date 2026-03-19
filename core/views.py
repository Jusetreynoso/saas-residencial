from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages 
from django.core.exceptions import ValidationError
import json 
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.utils import timezone   
from datetime import datetime, timedelta 
from decimal import Decimal

# --- IMPORTS PARA CORREO (Se mantienen por si activas a futuro) ---
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth.forms import SetPasswordForm, PasswordChangeForm 
from .forms import (
    ReservaForm, 
    LecturaGasForm, 
    GastoForm, 
    AvisoForm, 
    RegistroVecinoForm, 
    IncidenciaForm,
    EditarVecinoForm,
    AbonoForm,
    ReportePagoForm,
    IngresoExtraForm
)

from .models import Residencial, Reserva, Apartamento, Usuario, BloqueoFecha, Factura, LecturaGas, Gasto, Aviso, Incidencia, ReportePago, IngresoExtraordinario
from django.db.models import Sum, Max, Count, Q
from django.db.models.functions import TruncMonth
from itertools import chain
from operator import attrgetter


# ---------------------------------------------
# VISTA 1: El Dashboard
# ---------------------------------------------
@login_required
def dashboard(request):
    user = request.user
    context = {}

    # 1. LÓGICA PARA SUPER ADMIN
    if user.is_superuser:
        context['rol'] = 'Super Administrador'
        context['total_residenciales'] = Residencial.objects.count()
        context['residenciales'] = Residencial.objects.all()
        context['total_usuarios'] = Usuario.objects.count()
    
    # 2. LÓGICA PARA USUARIOS DEL RESIDENCIAL
    elif user.residencial:
        context['rol'] = user.get_rol_display()
        context['mi_residencial'] = user.residencial
        context['avisos'] = Aviso.objects.filter(residencial=user.residencial).order_by('-fecha_creacion')[:3]
        
        # --- ZONA DE ADMINISTRADOR ---
        if user.rol in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
            # A. Solicitudes Pendientes (Ya lo tenías)
            context['solicitudes_pendientes'] = Reserva.objects.filter(
                residencial=user.residencial, 
                estado='PENDIENTE'
            ).order_by('fecha_solicitud')

            # B. --- NUEVO: Reservas Aprobadas Futuras (Para poder cancelar) ---
            # Buscamos reservas aprobadas desde hoy en adelante
            context['reservas_futuras'] = Reserva.objects.filter(
                residencial=user.residencial,
                estado='APROBADA',
                fecha_solicitud__gte=timezone.now().date()
            ).order_by('fecha_solicitud')

        if user.apartamento:
            context['mi_apartamento'] = user.apartamento
        
        # Reservas PERSONALES (Lo que el usuario ve en "Mis Reservas")
        context['mis_reservas'] = Reserva.objects.filter(usuario=user).order_by('-fecha_solicitud')

        # MÓDULO DE FINANZAS
        mis_facturas = Factura.objects.filter(usuario=user).order_by('-fecha_emision')
        context['mis_facturas'] = mis_facturas
        context['total_pendiente'] = sum(
            (f.saldo_pendiente if f.saldo_pendiente is not None else f.monto) 
            for f in mis_facturas if f.estado == 'PENDIENTE'
        )

    else:
        context['mensaje'] = "Usuario sin residencial asignado."

    return render(request, 'core/dashboard.html', context)

# ---------------------------------------------
# VISTA 2: Crear Reserva
# ---------------------------------------------
@login_required
def crear_reserva(request):
    # =========================================================================
    # INICIO NUEVA REGLA: BLOQUEO POR MOROSIDAD
    # =========================================================================
    residencial = request.user.residencial
    
    # 1. Verificamos si el Edificio tiene activada la regla "bloquear_morosos"
    # Usamos getattr por seguridad, por si acaso no has corrido migraciones aún
    if getattr(residencial, 'bloquear_morosos', False):
        hoy = timezone.now().date()
        
        # 2. Buscamos si el usuario tiene CUOTAS de mantenimiento vencidas
        deuda_vencida = Factura.objects.filter(
            usuario=request.user,
            residencial=residencial,
            tipo='CUOTA',              # Solo mantenimiento (puedes quitar esta línea si quieres bloquear por Gas también)
            estado='PENDIENTE',        # Que deba dinero
            fecha_vencimiento__lt=hoy  # Que la fecha límite ya pasó
        ).exists()
        
        # 3. Si tiene deuda, lo bloqueamos y mandamos mensaje
        if deuda_vencida:
            messages.error(request, "⛔ Acceso denegado: Tienes cuotas de mantenimiento vencidas. Por favor, regulariza tu deuda para reservar áreas sociales.")
            return redirect('dashboard')
    # =========================================================================
    # FIN NUEVA REGLA (El resto del código sigue igual)
    # =========================================================================

    reservas_ocupadas = Reserva.objects.filter(
        residencial=request.user.residencial,
        estado__in=['PENDIENTE', 'APROBADA']
    ).values_list('fecha_solicitud', flat=True)
    
    fechas_disable = [fecha.strftime("%Y-%m-%d") for fecha in reservas_ocupadas]

    if request.method == 'POST':
        form = ReservaForm(request.user, request.POST)
        if form.is_valid():
            reserva = form.save(commit=False)
            reserva.usuario = request.user
            reserva.residencial = request.user.residencial
            
            try:
                reserva.full_clean() 
                reserva.save()
                messages.success(request, '¡Solicitud enviada correctamente!')
                return redirect('dashboard') 
            
            except ValidationError as e:
                error_msg = e.message_dict.get('__all__', [str(e)])[0]
                messages.error(request, error_msg)
    else:
        form = ReservaForm(request.user)

    return render(request, 'core/reserva_form.html', {
        'form': form,
        'fechas_disable_json': json.dumps(fechas_disable, cls=DjangoJSONEncoder)
    })

@login_required
def gestionar_reserva(request, reserva_id, accion):
    reserva = get_object_or_404(Reserva, pk=reserva_id, residencial=request.user.residencial)
    usuario = reserva.usuario
    
    if accion == 'aprobar':
        reserva.estado = 'APROBADA'
        messages.success(request, f'Reserva aprobada correctamente.')
        
    elif accion == 'rechazar':
        reserva.estado = 'RECHAZADA'
        messages.warning(request, f'Reserva rechazada.')

    reserva.save()
    return redirect('dashboard')


@login_required
def api_eventos(request):
    residencial = request.user.residencial
    
    # 1. Buscamos solo las reservas APROBADAS
    reservas = Reserva.objects.filter(
        residencial=residencial,
        estado='APROBADA'
    )
    
    # 2. Buscamos los bloqueos de fechas
    bloqueos = BloqueoFecha.objects.filter(residencial=residencial)
    
    eventos = []

    # --- PROCESAMIENTO DE RESERVAS ---
    for reserva in reservas:
        # Formateamos la hora para que sea legible (Ej: 02:00 PM - 06:00 PM)
        if reserva.hora_inicio and reserva.hora_fin:
            inicio = reserva.hora_inicio.strftime("%I:%M %p")
            fin = reserva.hora_fin.strftime("%I:%M %p")
            horario = f"({inicio} - {fin})"
        else:
            horario = "(Todo el día)"

        # Decidimos QUÉ mostrar según quién mira el calendario
        if request.user.rol in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
            # El ADMIN ve: "B-201 (02:00 PM - 06:00 PM)"
            numero_apto = reserva.usuario.apartamento.numero if (reserva.usuario and reserva.usuario.apartamento) else "Sin Apto"
            titulo = f"📅 {numero_apto} {horario}"
            color = '#0d6efd' # Azul
            
        elif reserva.usuario == request.user:
            # EL DUEÑO ve: "Tu Reserva (02:00 PM - 06:00 PM)"
            titulo = f"✅ Tu Reserva {horario}"
            color = '#198754' # Verde
            
        else:
            # EL VECINO ve: "Reservado (02:00 PM - 06:00 PM)"
            titulo = f"⛔ Reservado {horario}"
            color = '#dc3545' # Rojo

        # Agregamos el evento al calendario
        eventos.append({
            'title': titulo,
            'start': reserva.fecha_solicitud.isoformat(),
            'color': color,
            'allDay': True  # Muestra el bloque completo para indicar que el día ya tiene uso
        })

    # --- PROCESAMIENTO DE BLOQUEOS ---
    for bloqueo in bloqueos:
        eventos.append({
            'title': f"🔒 {bloqueo.motivo}",
            'start': bloqueo.fecha.strftime("%Y-%m-%d"),
            'color': '#212529', # Negro/Gris oscuro
            'allDay': True
        })

    return JsonResponse(eventos, safe=False)

@login_required
def cancelar_reserva(request, reserva_id):
    # 1. Buscamos la reserva
    reserva = get_object_or_404(Reserva, pk=reserva_id)

    # 2. Seguridad: Solo el dueño o el Admin pueden cancelar
    es_admin = request.user.rol in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']
    es_dueno = request.user == reserva.usuario

    if not (es_dueno or es_admin):
        messages.error(request, "No tienes permiso para cancelar esta reserva.")
        return redirect('dashboard')

    # Guardamos datos clave antes de borrarla (para el mensaje)
    fecha_reserva = reserva.fecha_solicitud
    nombre_area = reserva.area_social.nombre
    estado_anterior = reserva.estado
    usuario_reserva = reserva.usuario

    # 3. Lógica de "Aviso al Admin"
    # Si es el VECINO quien cancela una reserva que ya estaba APROBADA
    if es_dueno and not es_admin and estado_anterior == 'APROBADA':
        # Creamos una Incidencia automática para avisar al admin
        Incidencia.objects.create(
            residencial=request.user.residencial,
            usuario=request.user,
            titulo=f"⚠️ Cancelación Reserva: {nombre_area}",
            descripcion=f"El vecino {usuario_reserva.first_name} {usuario_reserva.last_name} (Apto {usuario_reserva.apartamento.numero}) canceló su reserva aprobada para el día {fecha_reserva}. La fecha ha quedado libre.",
            estado='PENDIENTE'
        )
        msg_extra = " Se ha notificado al administrador."
    else:
        msg_extra = ""

    # 4. Borramos la reserva (Liberamos el calendario)
    reserva.delete()

    messages.success(request, f"✅ Reserva para el {fecha_reserva} cancelada y liberada.{msg_extra}")
    return redirect('dashboard')

@login_required
def bloquear_fecha(request):
    if request.method == 'POST' and request.user.rol in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        fecha = request.POST.get('fecha_bloqueo')
        motivo = request.POST.get('motivo_bloqueo')
        residencial = request.user.residencial
        
        if fecha and motivo:
            BloqueoFecha.objects.create(
                residencial=residencial,
                fecha=fecha,
                motivo=motivo
            )
            messages.success(request, f'Fecha {fecha} bloqueada correctamente.')
        else:
            messages.error(request, 'Debes indicar fecha y motivo.')
            
    return redirect('dashboard')

# ---------------------------------------------
# VISTA: Registrar Lectura Gas (CORREO DESACTIVADO/SIMULADO)
# ---------------------------------------------
# En core/views.py

@login_required
def registrar_lectura_gas(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        messages.error(request, "No tienes permiso.")
        return redirect('dashboard')

    if request.method == 'POST':
        form = LecturaGasForm(request.user, request.POST)
        if form.is_valid():
            apartamento = form.cleaned_data['apartamento']
            mes_actual = timezone.now().month
            anio_actual = timezone.now().year
            
            # 1. Validar duplicados
            existe = LecturaGas.objects.filter(
                residencial=request.user.residencial,
                apartamento=apartamento,
                fecha_lectura__month=mes_actual,
                fecha_lectura__year=anio_actual
            ).exists()
            
            if existe:
                messages.error(request, f"⚠️ Ya facturaste al apto {apartamento.numero} en este mes.")
            else:
                lectura = form.save(commit=False)
                lectura.residencial = request.user.residencial
                
                # 2. Validar consistencia
                if lectura.lectura_actual < lectura.lectura_anterior:
                    messages.error(request, "⛔ Error: La lectura actual es menor a la anterior.")
                else:
                    lectura.save() 
                    residente = lectura.apartamento.habitantes.first()
                    
                    if residente:
                        consumo = lectura.lectura_actual - lectura.lectura_anterior
                        
                        # 3. Crear Factura
                        nueva_factura = Factura.objects.create(
                            residencial=request.user.residencial,
                            usuario=residente,
                            tipo='GAS',
                            concepto=f"Gas: {lectura.lectura_anterior} -> {lectura.lectura_actual} ({consumo:.2f} gls)",
                            monto=lectura.total_a_pagar,
                            fecha_vencimiento=timezone.now().date() + timedelta(days=15),
                            estado='PENDIENTE',
                            saldo_pendiente=lectura.total_a_pagar
                        )
                        
                        # 4. LÓGICA AUTOMÁTICA (AHORA SOLO TOCA SALDO DE GAS)
                        msg_extra = ""
                        # CAMBIO IMPORTANTE AQUÍ: Usamos saldo_favor_gas
                        if residente.saldo_favor_gas > 0:
                            if residente.saldo_favor_gas >= nueva_factura.monto:
                                residente.saldo_favor_gas -= nueva_factura.monto
                                nueva_factura.monto_pagado = nueva_factura.monto
                                nueva_factura.saldo_pendiente = 0
                                nueva_factura.estado = 'PAGADO'
                                nueva_factura.fecha_pago = timezone.now().date()
                                msg_extra = " (✅ Pagada con saldo de Gas)"
                            else:
                                abono = residente.saldo_favor_gas
                                residente.saldo_favor_gas = 0 
                                nueva_factura.monto_pagado = abono
                                nueva_factura.saldo_pendiente = nueva_factura.monto - abono
                                msg_extra = f" (💰 Se descontaron ${abono} de su saldo de Gas)"
        
                            residente.save()
                            nueva_factura.save()

                        lectura.factura_generada = nueva_factura
                        lectura.save()
                        messages.success(request, f"✅ Factura generada para {apartamento.numero}: ${lectura.total_a_pagar}{msg_extra}")
                    else:
                        messages.warning(request, f"⚠️ Lectura guardada, pero el apto {apartamento.numero} no tiene dueño asignado.")
                
            return redirect('registrar_lectura_gas')
    else:
        ultima_general = LecturaGas.objects.filter(residencial=request.user.residencial).last()
        precio = ultima_general.precio_galon_mes if ultima_general else 0.00
        form = LecturaGasForm(request.user, initial={'precio_galon_mes': precio})

    # Datos para la tabla y el script
    apartamentos = Apartamento.objects.filter(residencial=request.user.residencial).order_by('numero')
    estado_medidores = []

    for apt in apartamentos:
        ultima = LecturaGas.objects.filter(apartamento=apt).order_by('-fecha_lectura').first()
        datos = {
            'id': apt.id,
            'apto': apt.numero,
            'ultima_fecha': ultima.fecha_lectura if ultima else "---",
            'lectura_anterior': ultima.lectura_anterior if ultima else 0.000,
            'lectura_actual': ultima.lectura_actual if ultima else 0.000,
            'consumo': (ultima.lectura_actual - ultima.lectura_anterior) if ultima else 0.00,
            'galones': ultima.consumo_galones if ultima else 0.00,
            'precio': ultima.precio_galon_mes if ultima else 0.00,
            'total': ultima.total_a_pagar if ultima else 0.00,
        }
        estado_medidores.append(datos)

    return render(request, 'core/registrar_gas.html', {
        'form': form,
        'estado_medidores': estado_medidores
    })

# ---------------------------------------------
# VISTA: Generar Cuotas Masivas (CORREO DESACTIVADO/SIMULADO)
# ---------------------------------------------
# En core/views.py

@login_required
def generar_cuotas_masivas(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        messages.error(request, "Acceso denegado.")
        return redirect('dashboard')

    residencial = request.user.residencial
    mes_actual = timezone.now().month
    anio_actual = timezone.now().year
    
    apartamentos = Apartamento.objects.filter(residencial=residencial, monto_cuota__gt=0)
    
    contador = 0
    
    for apto in apartamentos:
        dueno = apto.habitantes.first()
        
        if dueno:
            existe = Factura.objects.filter(
                residencial=residencial,
                usuario=dueno,
                tipo='CUOTA',
                fecha_emision__month=mes_actual,
                fecha_emision__year=anio_actual
            ).exists()
            
            if not existe:
                # 1. Creamos la factura normalmente (PENDIENTE por defecto)
                nueva_factura = Factura.objects.create(
                    residencial=residencial,
                    usuario=dueno,
                    tipo='CUOTA',
                    concepto=f"Mantenimiento {timezone.now().strftime('%B %Y')}",
                    monto=apto.monto_cuota,
                    fecha_vencimiento=timezone.now().date() + timedelta(days=residencial.dias_gracia),
                    estado='PENDIENTE',
                    saldo_pendiente=apto.monto_cuota # Inicialmente debe todo
                )
                
                # 2. LÓGICA AUTOMÁTICA DE SALDO A FAVOR (CORREGIDA: SOLO MANTENIMIENTO)
                if dueno.saldo_favor_mantenimiento > 0:
                    
                    # CASO A: El saldo cubre toda la factura
                    if dueno.saldo_favor_mantenimiento >= nueva_factura.monto:
                        dueno.saldo_favor_mantenimiento -= nueva_factura.monto
                        nueva_factura.monto_pagado = nueva_factura.monto
                        nueva_factura.saldo_pendiente = 0
                        nueva_factura.estado = 'PAGADO'
                        nueva_factura.fecha_pago = timezone.now().date()
                        
                    # CASO B: El saldo es menor a la factura (Abono parcial)
                    else:
                        abono = dueno.saldo_favor_mantenimiento
                        dueno.saldo_favor_mantenimiento = 0 # Se gastó todo su saldo de mantenimiento
                        nueva_factura.monto_pagado = abono
                        nueva_factura.saldo_pendiente = nueva_factura.monto - abono
                        # Sigue en estado PENDIENTE, pero con menos deuda
                    
                    # Guardamos los cambios
                    dueno.save()
                    nueva_factura.save()

                contador += 1
    
    if contador > 0:
        messages.success(request, f"✅ Se generaron {contador} facturas (aplicando saldos de mantenimiento automáticamente).")
    else:
        messages.info(request, "ℹ️ No se generaron facturas nuevas.")
        
    return redirect('dashboard')

@login_required
def cuentas_por_cobrar(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')
    
    deudas = Factura.objects.filter(
        residencial=request.user.residencial,
        estado='PENDIENTE'
    ).order_by('usuario__apartamento__numero')
    
    total_por_cobrar = sum(f.monto for f in deudas)

    return render(request, 'core/cuentas_por_cobrar.html', {
        'deudas': deudas,
        'total_por_cobrar': total_por_cobrar,
        'today': timezone.now().date()
    })

# En core/views.py

@login_required
def registrar_pago(request, factura_id):
    factura = get_object_or_404(Factura, pk=factura_id, residencial=request.user.residencial)
    
    if request.method == 'POST':
        monto_recibido = Decimal(request.POST.get('monto_pagado', 0))
        
        if monto_recibido <= 0:
            messages.error(request, "⚠️ El monto debe ser mayor a 0.")
            return redirect('cuentas_por_cobrar')

        # Calculamos cuánto falta por pagar realmente
        deuda_actual = factura.saldo_pendiente if factura.saldo_pendiente is not None else factura.monto
        
        # 1. Registramos el pago acumulado
        factura.monto_pagado = (factura.monto_pagado or 0) + monto_recibido
        
        # CASO A: Pagó la deuda completa (o pagó de más)
        if monto_recibido >= deuda_actual:
            factura.estado = 'PAGADO'
            factura.saldo_pendiente = 0
            factura.fecha_pago = timezone.now().date()
            
            # Calculamos si sobró dinero
            sobrante = monto_recibido - deuda_actual
            
            if sobrante > 0:
                vecino = factura.usuario
                
                # --- CORRECCIÓN CLAVE: DETECTAMOS EL BOLSILLO AUTOMÁTICAMENTE ---
                bolsillo_nombre = ""
                
                if factura.tipo == 'GAS':
                    # Si la factura era de Gas, el vuelto va al saldo de Gas
                    saldo_actual = vecino.saldo_favor_gas or Decimal(0)
                    vecino.saldo_favor_gas = saldo_actual + sobrante
                    bolsillo_nombre = "Gas"
                else:
                    # Si era Cuota o Mantenimiento, va al saldo de Mantenimiento
                    saldo_actual = vecino.saldo_favor_mantenimiento or Decimal(0)
                    vecino.saldo_favor_mantenimiento = saldo_actual + sobrante
                    bolsillo_nombre = "Mantenimiento"
                
                vecino.save() # Guardamos el saldo en el vecino
                
                messages.success(request, f"✅ Pagado. Se abonaron ${sobrante:,.2f} al saldo de {bolsillo_nombre} de {vecino.first_name}.")
            else:
                messages.success(request, f"✅ Factura pagada correctamente (Exacto).")

        # CASO B: Pago Parcial (Abono)
        else:
            factura.saldo_pendiente = deuda_actual - monto_recibido
            # Si es parcial, no tocamos fechas de pago final ni estados de pagado
            messages.warning(request, f"💰 Abono registrado. Restan por pagar: ${factura.saldo_pendiente:,.2f}")

        factura.save()
        
    return redirect('cuentas_por_cobrar')

@login_required
def registrar_gasto(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    if request.method == 'POST':
        form = GastoForm(request.POST)
        if form.is_valid():
            gasto = form.save(commit=False)
            gasto.residencial = request.user.residencial
            gasto.save()
            messages.success(request, f"📉 Gasto registrado: {gasto.descripcion} - ${gasto.monto}")
            return redirect('registrar_gasto')
    else:
        form = GastoForm(initial={'fecha_gasto': timezone.now().date()})

    ultimos_gastos = Gasto.objects.filter(residencial=request.user.residencial).order_by('-fecha_gasto')[:10]
    
    mes_actual = timezone.now().month
    total_mes = Gasto.objects.filter(
        residencial=request.user.residencial, 
        fecha_gasto__month=mes_actual
    ).aggregate(Sum('monto'))['monto__sum'] or 0

    return render(request, 'core/registrar_gasto.html', {
        'form': form,
        'ultimos_gastos': ultimos_gastos,
        'total_mes': total_mes
    })


@login_required
def reporte_financiero(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    residencial = request.user.residencial
    anio_actual = timezone.now().year
    mes_actual = timezone.now().month

    # 1. TOTALES ANUALES
    ingresos_facturas = Factura.objects.filter(
        residencial=residencial, 
        estado='PAGADO', 
        fecha_pago__year=anio_actual
    ).aggregate(Sum('monto'))['monto__sum'] or 0

    # CORRECCIÓN AQUÍ: Usamos 'Apartamento__residencial' y 'fecha_pago'
    ingresos_extra_anual = IngresoExtraordinario.objects.filter(
        Apartamento__residencial=residencial, # <--- CAMBIO CLAVE
        fecha_pago__year=anio_actual
    ).aggregate(Sum('monto'))['monto__sum'] or 0

    total_ingresos = ingresos_facturas + ingresos_extra_anual

    total_gastos = Gasto.objects.filter(
        residencial=residencial, 
        fecha_gasto__year=anio_actual
    ).aggregate(Sum('monto'))['monto__sum'] or 0

    balance = total_ingresos - total_gastos

    # 2. DATOS PARA GRÁFICOS
    ingresos_qs = Factura.objects.filter(
        residencial=residencial,
        estado='PAGADO',
        fecha_pago__year=anio_actual
    ).annotate(mes=TruncMonth('fecha_pago')).values('mes').annotate(total=Sum('monto')).order_by('mes')

    # CORRECCIÓN AQUÍ TAMBIÉN
    ingresos_extra_qs = IngresoExtraordinario.objects.filter(
        Apartamento__residencial=residencial, # <--- CAMBIO CLAVE
        fecha_pago__year=anio_actual
    ).annotate(mes=TruncMonth('fecha_pago')).values('mes').annotate(total=Sum('monto')).order_by('mes')

    gastos_qs = Gasto.objects.filter(
        residencial=residencial,
        fecha_gasto__year=anio_actual
    ).annotate(mes=TruncMonth('fecha_gasto')).values('mes').annotate(total=Sum('monto')).order_by('mes')

    datos_por_mes = {}
    
    for i in ingresos_qs:
        mes_str = i['mes'].strftime('%B') 
        if mes_str not in datos_por_mes: datos_por_mes[mes_str] = {'ingreso': 0, 'gasto': 0}
        datos_por_mes[mes_str]['ingreso'] += float(i['total'])

    for ie in ingresos_extra_qs:
        mes_str = ie['mes'].strftime('%B')
        if mes_str not in datos_por_mes: datos_por_mes[mes_str] = {'ingreso': 0, 'gasto': 0}
        datos_por_mes[mes_str]['ingreso'] += float(ie['total'])

    for g in gastos_qs:
        mes_str = g['mes'].strftime('%B')
        if mes_str not in datos_por_mes: datos_por_mes[mes_str] = {'ingreso': 0, 'gasto': 0}
        datos_por_mes[mes_str]['gasto'] = float(g['total'])

    bar_labels = list(datos_por_mes.keys())
    bar_ingresos = [d['ingreso'] for d in datos_por_mes.values()]
    bar_gastos = [d['gasto'] for d in datos_por_mes.values()]

    ingresos_tipo = list(Factura.objects.filter(
        residencial=residencial,
        estado='PAGADO',
        fecha_pago__year=anio_actual
    ).values('tipo').annotate(total=Sum('monto')))

    pie_labels = [item['tipo'] for item in ingresos_tipo]
    pie_data = [float(item['total']) for item in ingresos_tipo]
    
    if ingresos_extra_anual > 0:
        pie_labels.append("Extraordinarios")
        pie_data.append(float(ingresos_extra_anual))

    # 3. DATOS PARA LIBRO DIARIO
    ingresos_historicos = Factura.objects.filter(
        residencial=residencial, 
        estado='PAGADO', 
        fecha_pago__lt=timezone.datetime(anio_actual, mes_actual, 1)
    ).aggregate(Sum('monto'))['monto__sum'] or 0

    # CORRECCIÓN AQUÍ
    ingresos_extra_historicos = IngresoExtraordinario.objects.filter(
        Apartamento__residencial=residencial, # <--- CAMBIO CLAVE
        fecha_pago__lt=timezone.datetime(anio_actual, mes_actual, 1)
    ).aggregate(Sum('monto'))['monto__sum'] or 0
    
    gastos_historicos = Gasto.objects.filter(
        residencial=residencial, 
        fecha_gasto__lt=timezone.datetime(anio_actual, mes_actual, 1)
    ).aggregate(Sum('monto'))['monto__sum'] or 0

    saldo_inicial_mes = residencial.saldo_inicial + ingresos_historicos + ingresos_extra_historicos - gastos_historicos
    saldo_acumulado = saldo_inicial_mes 

    mov_ingresos = Factura.objects.filter(
        residencial=residencial, 
        estado='PAGADO', 
        fecha_pago__year=anio_actual, 
        fecha_pago__month=mes_actual
    )

    # CORRECCIÓN AQUÍ
    mov_extras = IngresoExtraordinario.objects.filter(
        Apartamento__residencial=residencial, # <--- CAMBIO CLAVE
        fecha_pago__year=anio_actual,
        fecha_pago__month=mes_actual
    )

    mov_gastos = Gasto.objects.filter(
        residencial=residencial, 
        fecha_gasto__year=anio_actual, 
        fecha_gasto__month=mes_actual
    )

    # Normalización para la tabla
    for i in mov_ingresos: 
        i.tipo_mov = 'INGRESO'
        i.fecha_mov = i.fecha_pago
        i.concepto_tabla = i.concepto

    for e in mov_extras:
        e.tipo_mov = 'INGRESO'
        e.fecha_mov = e.fecha_pago
        e.concepto_tabla = f"💰 EXTRA: {e.concepto_detalle}"
        e.monto = e.monto 
        # Como el modelo no tiene 'usuario', usamos el dueño del apartamento si existe
        if e.Apartamento and e.Apartamento.habitantes.exists():
             e.usuario_display = e.Apartamento.habitantes.first().username
        else:
             e.usuario_display = "Externo/Admin"

    for g in mov_gastos: 
        g.tipo_mov = 'GASTO'
        g.fecha_mov = g.fecha_gasto
        g.concepto_tabla = g.descripcion

    lista_movimientos = sorted(
        chain(mov_ingresos, mov_extras, mov_gastos), 
        key=attrgetter('fecha_mov')
    )

    tabla_movimientos = []
    for mov in lista_movimientos:
        if mov.tipo_mov == 'INGRESO':
            saldo_acumulado += mov.monto
        else:
            saldo_acumulado -= mov.monto
        
        # Determinamos qué nombre de usuario mostrar
        if hasattr(mov, 'usuario_display'):
            user_show = mov.usuario_display
        elif hasattr(mov, 'usuario') and mov.usuario:
            user_show = mov.usuario.username
        else:
            user_show = 'Admin'

        tabla_movimientos.append({
            'fecha': mov.fecha_mov,
            'concepto': mov.concepto_tabla,
            'tipo': mov.tipo_mov,
            'monto': mov.monto,
            'saldo': saldo_acumulado,
            'usuario': user_show
        })

    context = {
        'anio': anio_actual,
        'mes_nombre': timezone.now().strftime('%B'),
        'total_ingresos': total_ingresos,
        'total_gastos': total_gastos,
        'balance': balance,
        'bar_labels': json.dumps(bar_labels),
        'bar_ingresos': json.dumps(bar_ingresos),
        'bar_gastos': json.dumps(bar_gastos),
        'pie_labels': json.dumps(pie_labels),
        'pie_data': json.dumps(pie_data),
        'saldo_inicial_banco': residencial.saldo_inicial,
        'saldo_arranque_mes': saldo_inicial_mes,
        'tabla_movimientos': tabla_movimientos,
        'saldo_final_mes': saldo_acumulado
    }

    return render(request, 'core/reporte_financiero.html', context)

@login_required
def crear_aviso(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')
        
    if request.method == 'POST':
        form = AvisoForm(request.POST)
        if form.is_valid():
            aviso = form.save(commit=False)
            aviso.residencial = request.user.residencial
            aviso.save()
            messages.success(request, "📢 Aviso publicado correctamente.")
            return redirect('dashboard')
    
    return redirect('dashboard') 

@login_required
def borrar_aviso(request, aviso_id):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')
    
    aviso = get_object_or_404(Aviso, pk=aviso_id, residencial=request.user.residencial)
    aviso.delete()
    messages.success(request, "🗑️ Aviso eliminado.")
    return redirect('dashboard')

@login_required
def ver_recibo(request, factura_id):
    factura = get_object_or_404(Factura, pk=factura_id)

    es_dueno = (factura.usuario == request.user)
    es_admin = (request.user.rol in ['ADMIN_RESIDENCIAL', 'SUPERADMIN'] and request.user.residencial == factura.residencial)

    if not (es_dueno or es_admin):
        messages.error(request, "No tienes permiso para ver este recibo.")
        return redirect('dashboard')

    if factura.estado != 'PAGADO':
        messages.warning(request, "Esta factura aún no ha sido pagada, no tiene recibo.")
        return redirect('dashboard')

    return render(request, 'core/recibo_print.html', {'factura': factura})




@login_required
def lista_vecinos(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')
    
    vecinos = Usuario.objects.filter(residencial=request.user.residencial).order_by('apartamento__numero')
    
    # --- LÓGICA NUEVA: CALCULAR DEUDA POR VECINO ---
    for vecino in vecinos:
        # Buscamos sus facturas pendientes
        deudas = Factura.objects.filter(usuario=vecino, estado='PENDIENTE')
        
        # Sumamos: Si tiene saldo_pendiente usamos eso, si no, usamos el monto total
        total_deuda = sum(
            (f.saldo_pendiente if f.saldo_pendiente is not None else f.monto) 
            for f in deudas
        )
        
        # "Pegamos" este dato temporalmente al vecino para usarlo en el HTML
        vecino.deuda_calculada = total_deuda
    # -----------------------------------------------
    
    return render(request, 'core/lista_vecinos.html', {'vecinos': vecinos})

@login_required
def crear_vecino(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    if request.method == 'POST':
        form = RegistroVecinoForm(request.user, request.POST)
        if form.is_valid():
            # 1. Crear el usuario base
            nuevo_usuario = form.save(commit=False)
            nuevo_usuario.residencial = request.user.residencial 
            nuevo_usuario.rol = 'RESIDENTE' 
            
            # Guardamos teléfono manualmente
            nuevo_usuario.telefono = form.cleaned_data.get('telefono')
            
            # 2. Asignar Apartamento (Si seleccionó uno)
            apto = form.cleaned_data.get('apartamento')
            if apto:
                nuevo_usuario.apartamento = apto
            
            nuevo_usuario.save()
            messages.success(request, f"✅ Vecino {nuevo_usuario.username} registrado correctamente.")
            return redirect('lista_vecinos')
    else:
        form = RegistroVecinoForm(request.user)


    # ---------------------------------------------------------------
    # LÓGICA INTELIGENTE: PREPARAR DATOS
    # ---------------------------------------------------------------
    apartamentos = Apartamento.objects.filter(residencial=request.user.residencial)
    datos_inteligentes = {}

    print("--- INICIO DIAGNÓSTICO GAS ---") 
    for apt in apartamentos:
        # Buscamos la última lectura registrada
        ultima = LecturaGas.objects.filter(apartamento=apt).order_by('-id').first()
        
        if ultima:
            datos_inteligentes[apt.id] = float(ultima.lectura_actual)
            print(f"✅ Apto {apt.numero} (ID {apt.id}): Última lectura encontrada -> {ultima.lectura_actual}")
        else:
            datos_inteligentes[apt.id] = 0.00
            print(f"⚠️ Apto {apt.numero} (ID {apt.id}): No tiene historial de lecturas.")
    print("------------------------------")

    # Convertimos a JSON
    datos_json = json.dumps(datos_inteligentes, cls=DjangoJSONEncoder)

    return render(request, 'core/crear_vecino_form.html', {'form': form})

# 1. PARA EL VECINO: CREAR REPORTE
@login_required
def crear_incidencia(request):
    if request.method == 'POST':
        form = IncidenciaForm(request.POST, request.FILES)
        if form.is_valid():
            incidencia = form.save(commit=False)
            incidencia.residencial = request.user.residencial
            incidencia.usuario = request.user
            incidencia.save()
            messages.success(request, "🛠️ Reporte enviado. La administración lo revisará pronto.")
            return redirect('dashboard')
    
    return redirect('dashboard')

# 2. PARA EL ADMIN: GESTIONAR REPORTES
@login_required
def gestionar_incidencias(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')
        
    # Procesar cambio de estado
    if request.method == 'POST':
        incidencia_id = request.POST.get('incidencia_id')
        nuevo_estado = request.POST.get('nuevo_estado')
        comentario = request.POST.get('comentario')
        
        incidencia = get_object_or_404(Incidencia, pk=incidencia_id, residencial=request.user.residencial)
        incidencia.estado = nuevo_estado
        incidencia.comentario_admin = comentario
        incidencia.save()
        messages.success(request, f"✅ Estado actualizado a: {incidencia.get_estado_display()}")
        return redirect('gestionar_incidencias')

    # Listar incidencias (Pendientes primero)
    incidencias = Incidencia.objects.filter(residencial=request.user.residencial).order_by('estado', '-fecha_creacion')
    
    return render(request, 'core/gestionar_incidencias.html', {'incidencias': incidencias})

# ---------------------------------------------
# NUEVAS VISTAS: EDITAR Y CAMBIAR CLAVE
# ---------------------------------------------
@login_required
def editar_vecino(request, user_id):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')
    
    vecino = get_object_or_404(Usuario, pk=user_id, residencial=request.user.residencial)

    if request.method == 'POST':
        form = EditarVecinoForm(request.POST, instance=vecino)
        if form.is_valid():
            form.save()
            messages.success(request, f"✅ Datos de {vecino.first_name} actualizados.")
            return redirect('lista_vecinos')
    else:
        form = EditarVecinoForm(instance=vecino)

    return render(request, 'core/vecino_form_edit.html', {'form': form, 'vecino': vecino})

# En core/views.py

@login_required
def cambiar_clave_vecino(request, user_id):
    # Seguridad: Solo admin puede entrar
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    vecino = get_object_or_404(Usuario, pk=user_id, residencial=request.user.residencial)

    if request.method == 'POST':
        form = SetPasswordForm(vecino, request.POST)
        if form.is_valid():
            # --- CAMBIO: GUARDADO MANUAL "A LA FUERZA" ---
            # En lugar de form.save(), tomamos el dato y lo inyectamos nosotros.
            nueva_clave = form.cleaned_data['new_password1']
            vecino.set_password(nueva_clave)
            vecino.save()
            # ---------------------------------------------
            
            messages.success(request, f"🔑 Contraseña de {vecino.username} cambiada exitosamente.")
            return redirect('lista_vecinos')
    else:
        form = SetPasswordForm(vecino)

    return render(request, 'core/vecino_password_form.html', {'form': form, 'vecino': vecino})

# En core/views.py

# En core/views.py

from decimal import Decimal

@login_required
def aplicar_moras(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    residencial = request.user.residencial
    hoy = timezone.now().date()
    
    # 1. Blindaje del porcentaje (Aseguramos que sea un número Decimal perfecto)
    porcentaje_str = str(residencial.porcentaje_mora or 0)
    porcentaje = Decimal(porcentaje_str)
    
    if porcentaje <= 0:
        messages.warning(request, "⚠️ No tienes configurado el porcentaje de mora en la configuración del Residencial.")
        return redirect('dashboard')

    # 2. Buscar facturas vencidas de mantenimiento (Tienen que ser 'CUOTA' y estar 'PENDIENTE')
    facturas_pendientes = Factura.objects.filter(
        residencial=residencial,
        tipo='CUOTA',
        estado='PENDIENTE',
        fecha_vencimiento__lt=hoy
    )
    
    contador_aplicadas = 0
    total_vencidas = facturas_pendientes.count() # Contamos cuántas encontró realmente

    for factura in facturas_pendientes:
        aplicar = False
        
        # CASO A: Nunca se le ha cobrado mora (aplica de inmediato porque ya venció)
        if factura.fecha_ultima_mora is None:
            aplicar = True
            
        # CASO B: Ya tiene mora previa, verificamos si ya pasaron los 30 días
        else:
            dias_pasados = (hoy - factura.fecha_ultima_mora).days
            if dias_pasados >= 30:
                aplicar = True

        # --- APLICAMOS EL CASTIGO ---
        if aplicar:
            # Cálculo matemático protegido (Decimal * Decimal)
            recargo = factura.saldo_pendiente * (porcentaje / Decimal('100'))
            
            factura.monto += recargo
            factura.saldo_pendiente += recargo
            factura.concepto += f" (+{porcentaje}% Mora)"
            factura.fecha_ultima_mora = hoy 
            
            factura.save()
            contador_aplicadas += 1

    # --- 3. MENSAJES INTELIGENTES DE RESPUESTA ---
    if contador_aplicadas > 0:
        messages.success(request, f"✅ ¡Éxito! Se calculó y aplicó mora a {contador_aplicadas} cuotas vencidas.")
    elif total_vencidas > 0:
        messages.info(request, f"⏳ El sistema detectó {total_vencidas} facturas vencidas, pero a ninguna le toca mora hoy (aún no cumplen los 30 días exactos desde su última mora).")
    else:
        messages.warning(request, "🕵️‍♂️ El sistema NO encontró facturas de mantenimiento vencidas. Revisa que las cuotas pendientes sean del tipo 'Mantenimiento' y que su fecha de vencimiento ya haya pasado.")

    return redirect('dashboard')

@login_required
def registrar_abono(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    # LÓGICA CUANDO SE ENVÍAN DATOS (POST)
    if request.method == 'POST':
        usuario_id = request.POST.get('usuario')
        monto = Decimal(request.POST.get('monto'))
        concepto = request.POST.get('concepto')
        tipo_pago = request.POST.get('tipo_pago') # 'GAS' o 'MANTENIMIENTO'

        vecino = get_object_or_404(Usuario, pk=usuario_id)
        monto_disponible = monto

        # 1. INTENTAR PAGAR DEUDAS EXISTENTES PRIMERO
        filtro_tipo = 'GAS' if tipo_pago == 'GAS' else 'CUOTA'
        
        facturas_pendientes = Factura.objects.filter(
            usuario=vecino,
            estado='PENDIENTE',
            tipo=filtro_tipo
        ).order_by('fecha_vencimiento')

        facturas_pagadas_count = 0

        for factura in facturas_pendientes:
            if monto_disponible <= 0: break

            deuda = factura.saldo_pendiente
            
            if monto_disponible >= deuda:
                monto_disponible -= deuda
                factura.saldo_pendiente = 0
                factura.monto_pagado = factura.monto
                factura.estado = 'PAGADO'
                factura.fecha_pago = timezone.now().date()
                factura.save()
                facturas_pagadas_count += 1
            else:
                factura.saldo_pendiente -= monto_disponible
                factura.monto_pagado += monto_disponible
                monto_disponible = 0
                factura.save()

        # 2. EL SOBRANTE (O EL TOTAL SI NO HABÍA DEUDA) VA AL SALDO A FAVOR
        msg_extra = ""
        if monto_disponible > 0:
            if tipo_pago == 'GAS':
                vecino.saldo_favor_gas += monto_disponible
                bolsillo = "Gas"
            else:
                vecino.saldo_favor_mantenimiento += monto_disponible
                bolsillo = "Mantenimiento"
            
            vecino.save()
            msg_extra = f"y se abonaron ${monto_disponible} al saldo de {bolsillo}."
        else:
            msg_extra = "cubriendo deuda pendiente."

        messages.success(request, f"✅ Abono registrado a {vecino}. Se pagaron {facturas_pagadas_count} facturas {msg_extra}")
        
        # Si venía del modal de cuentas por cobrar, volvemos allí. Si no, al dashboard.
        if 'next' in request.POST:
             return redirect(request.POST.get('next'))
        return redirect('cuentas_por_cobrar')

    # LÓGICA CUANDO ENTRAS A LA PANTALLA (GET)
    else:
        # Usamos el formulario para facilitar la lista de vecinos
        form = AbonoForm(request.user)
        
    return render(request, 'core/registrar_abono.html', {'form': form})


# 1. VISTA PARA EL VECINO (SUBIR PAGO)
@login_required
def reportar_pago(request):
    if request.method == 'POST':
        form = ReportePagoForm(request.POST, request.FILES)
        if form.is_valid():
            reporte = form.save(commit=False)
            reporte.usuario = request.user
            reporte.residencial = request.user.residencial
            reporte.save()
            messages.success(request, "📸 Comprobante enviado. Espera la confirmación del administrador.")
            return redirect('dashboard')
    else:
        form = ReportePagoForm()
    
    return render(request, 'core/reportar_pago.html', {'form': form})

# 2. VISTA PARA EL ADMIN (GESTIONAR REPORTES)
@login_required
def gestionar_reportes_pago(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    if request.method == 'POST':
        reporte_id = request.POST.get('reporte_id')
        accion = request.POST.get('accion') 
        
        reporte = get_object_or_404(ReportePago, pk=reporte_id, residencial=request.user.residencial)
        
        if reporte.estado == 'PENDIENTE':
            if accion == 'aprobar':
                vecino = reporte.usuario
                monto_disponible = reporte.monto
                tipo_pago = reporte.tipo_pago  # ¿Qué está pagando?

                # 1. FILTRO INTELIGENTE
                # Si es GAS, buscamos facturas de GAS. Si es MANTENIMIENTO, buscamos CUOTAS.
                filtro_tipo = 'GAS' if tipo_pago == 'GAS' else 'CUOTA'
                
                # Buscamos facturas viejas DE ESE TIPO
                facturas_pendientes = Factura.objects.filter(
                    usuario=vecino, 
                    estado='PENDIENTE',
                    tipo=filtro_tipo 
                ).order_by('fecha_vencimiento')

                facturas_pagadas = 0

                # Algoritmo Mata-Deudas (FIFO)
                for factura in facturas_pendientes:
                    if monto_disponible <= 0: break 

                    deuda_factura = factura.saldo_pendiente

                    if monto_disponible >= deuda_factura:
                        monto_disponible -= deuda_factura
                        factura.saldo_pendiente = 0
                        factura.estado = 'PAGADO'
                        factura.monto_pagado = factura.monto 
                        factura.fecha_pago = timezone.now().date()
                        factura.save()
                        facturas_pagadas += 1
                    else:
                        factura.saldo_pendiente -= monto_disponible
                        monto_disponible = 0 
                        factura.save()
                
                # 2. EL SOBRANTE VA AL BOLSILLO CORRECTO
                bolsillo_nombre = ""
                if monto_disponible > 0:
                    if tipo_pago == 'GAS':
                        vecino.saldo_favor_gas += monto_disponible
                        bolsillo_nombre = "GAS"
                    else:
                        # Mantenimiento u Otro va al saldo principal
                        vecino.saldo_favor_mantenimiento += monto_disponible
                        bolsillo_nombre = "MANTENIMIENTO"
                    
                    vecino.save()
                    msg_extra = f"y sobraron ${monto_disponible} al saldo de {bolsillo_nombre}."
                else:
                    msg_extra = "cubriendo deuda pendiente."

                reporte.estado = 'APROBADO'
                reporte.comentario_admin = f"Pago aplicado a {filtro_tipo}. Se pagaron {facturas_pagadas} facturas."
                reporte.save()

                messages.success(request, f"Pago de {vecino.first_name} aplicado exitosamente {msg_extra}")
                
            elif accion == 'rechazar':
                reporte.estado = 'RECHAZADO'
                reporte.save()
                messages.warning(request, "Reporte de pago rechazado.")
            
            return redirect('gestionar_reportes_pago')

    reportes = ReportePago.objects.filter(residencial=request.user.residencial).order_by('estado', '-fecha_reporte')
    return render(request, 'core/gestionar_reportes.html', {'reportes': reportes})

# 3. VISTA "MATRIZ FINANCIERA" (LO QUE PEDISTE)
@login_required
def balance_residencial(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    apartamentos = Apartamento.objects.filter(residencial=request.user.residencial).order_by('numero')
    
    data_financiera = []
    
    # Acumuladores globales
    total_deuda_global = 0
    total_favor_mant_global = 0  # Nuevo acumulador Mant.
    total_favor_gas_global = 0   # Nuevo acumulador Gas

    for apt in apartamentos:
        dueno = apt.habitantes.first()
        
        deuda = 0
        saldo_mant = 0
        saldo_gas = 0
        nombre_dueno = "--- Sin Asignar ---"
        
        if dueno:
            nombre_dueno = f"{dueno.first_name} {dueno.last_name}"
            
            # --- CORRECCIÓN CLAVE: Leemos los dos bolsillos nuevos ---
            saldo_mant = dueno.saldo_favor_mantenimiento or 0
            saldo_gas = dueno.saldo_favor_gas or 0
            
            # Calculamos deuda real (Sumando saldos pendientes)
            facturas_pendientes = Factura.objects.filter(usuario=dueno, estado='PENDIENTE')
            deuda = sum(f.saldo_pendiente for f in facturas_pendientes)

        # Agregamos los datos desglosados a la lista
        data_financiera.append({
            'apto': apt.numero,
            'dueno': nombre_dueno,
            'deuda': deuda,
            'saldo_mant': saldo_mant, # Columna nueva
            'saldo_gas': saldo_gas,   # Columna nueva
            'estado': 'Moroso' if deuda > 0 else 'Al día'
        })
        
        # Sumamos a los totales globales
        total_deuda_global += deuda
        total_favor_mant_global += saldo_mant
        total_favor_gas_global += saldo_gas

    return render(request, 'core/balance_residencial.html', {
        'data': data_financiera,
        'total_deuda': total_deuda_global,
        'total_mant': total_favor_mant_global, # Pasamos total Mant.
        'total_gas': total_favor_gas_global    # Pasamos total Gas.
    })

@login_required
def registrar_ingreso_extraordinario(request):
    if request.method == 'POST':
        form = IngresoExtraForm(request.POST)
        if form.is_valid():
            ingreso = form.save(commit=False)
            # ELIMINAMOS LA LÍNEA: ingreso.residencial = ... (NO EXISTE)
            # El residencial se deduce automáticamente del Apartamento seleccionado
            ingreso.save()
            messages.success(request, "¡Ingreso extraordinario registrado con éxito!")
            return redirect('dashboard')
    else:
        form = IngresoExtraForm()
    
    return render(request, 'core/registrar_ingreso_extra.html', {'form': form})

@login_required
def cambiar_mi_clave(request):
    if request.method == 'POST':
        # PasswordChangeForm requiere el usuario actual y los datos del POST
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            # Esta línea es CLAVE: evita que el usuario cierre sesión al cambiar la clave
            update_session_auth_hash(request, user) 
            messages.success(request, '✅ Tu contraseña ha sido actualizada exitosamente.')
            return redirect('dashboard')
        else:
            messages.error(request, '⚠️ Hubo un error. Revisa los datos ingresados.')
    else:
        form = PasswordChangeForm(request.user)
        
    return render(request, 'core/cambiar_mi_clave.html', {'form': form})

@login_required
def reporte_gas_whatsapp(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')
        
    residencial = request.user.residencial
    
    # 1. Obtener la lectura más reciente para saber qué mes estamos reportando
    ultima_lectura_global = LecturaGas.objects.filter(residencial=residencial).order_by('-fecha_lectura').first()
    
    if not ultima_lectura_global:
        messages.warning(request, "⚠️ No hay lecturas de gas registradas para generar el reporte.")
        return redirect('dashboard')
        
    mes_reporte = ultima_lectura_global.fecha_lectura.month
    anio_reporte = ultima_lectura_global.fecha_lectura.year
    precio_galon = ultima_lectura_global.precio_galon_mes
    
    # Nombres de meses en español
    meses = ['', 'ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE']
    nombre_mes = f"{meses[mes_reporte]} {anio_reporte}"
    
    apartamentos = Apartamento.objects.filter(residencial=residencial).order_by('numero')
    
    # 2. Agrupar por edificio (Primera letra del apto, ej: "A" de "A-101")
    datos_por_edificio = {}
    total_general_galones = Decimal('0.00')
    total_general_pagar = Decimal('0.00')
    
    for apt in apartamentos:
        edificio = apt.numero[0] if apt.numero else "Otros"
        if edificio not in datos_por_edificio:
            datos_por_edificio[edificio] = {
                'apartamentos': [],
                'subtotal_galones': Decimal('0.00'),
                'subtotal_pagar': Decimal('0.00')
            }
            
        dueno = apt.habitantes.first()
        
        # Buscar la lectura de este mes para este apto
        lectura_mes = LecturaGas.objects.filter(
            apartamento=apt, 
            fecha_lectura__month=mes_reporte, 
            fecha_lectura__year=anio_reporte
        ).first()
        
        galones = lectura_mes.consumo_galones if lectura_mes else Decimal('0.00')
        costo_mes = galones * precio_galon
        
        deuda_total_gas = Decimal('0.00')
        saldo_favor = Decimal('0.00')
        
        if dueno:
            facturas_gas = Factura.objects.filter(usuario=dueno, tipo='GAS', estado='PENDIENTE')
            deuda_total_gas = sum((f.saldo_pendiente or f.monto) for f in facturas_gas)
            saldo_favor = dueno.saldo_favor_gas or Decimal('0.00')
            
        a_pagar = deuda_total_gas
        
        # 3. Lógica del Balance (Igual a tu Excel)
        if saldo_favor > 0:
            # En tu Excel el saldo a favor sale en negativo
            balance_txt = f"-${saldo_favor:.2f}" 
            color_balance = "text-success fw-bold"
        else:
            # Verificamos si debe de meses anteriores
            deuda_anterior = deuda_total_gas - costo_mes
            if deuda_anterior > 0:
                balance_txt = f"${deuda_anterior:.2f}"
                color_balance = "text-danger fw-bold"
            else:
                balance_txt = "$0.00"
                color_balance = "text-muted"
                
        # Solo lo agregamos al reporte si consumió gas o si debe dinero
        if galones > 0 or a_pagar > 0:
            datos_por_edificio[edificio]['apartamentos'].append({
                'numero': apt.numero,
                'galones': galones,
                'balance_txt': balance_txt,
                'color_balance': color_balance,
                'a_pagar': a_pagar
            })
            
            datos_por_edificio[edificio]['subtotal_galones'] += galones
            datos_por_edificio[edificio]['subtotal_pagar'] += a_pagar
            
            total_general_galones += galones
            total_general_pagar += a_pagar

    context = {
        'nombre_mes': nombre_mes,
        'precio_galon': precio_galon,
        'datos_por_edificio': datos_por_edificio,
        'total_general_galones': total_general_galones,
        'total_general_pagar': total_general_pagar,
        'residencial': residencial.nombre.upper()
    }
    
    return render(request, 'core/reporte_gas_whatsapp.html', context)

@login_required
def menu_reportes(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')
    
    return render(request, 'core/menu_reportes.html')

@login_required
def reporte_mensual_dinamico(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    residencial = request.user.residencial
    
    # 1. Obtener mes y año seleccionados (por defecto el mes actual)
    hoy = timezone.now()
    mes_seleccionado = int(request.GET.get('mes', hoy.month))
    
    # Capturamos el año como texto y le borramos cualquier espacio invisible (\xa0), espacio normal o coma
    anio_raw = str(request.GET.get('anio', hoy.year)).replace('\xa0', '').replace(' ', '').replace(',', '')
    anio_seleccionado = int(anio_raw)

    # --- LÓGICA DE CUADRE DE BANCO (POST) ---
    if request.method == 'POST' and 'cuadrar_banco' in request.POST:
        balance_real = Decimal(request.POST.get('balance_real', 0))
        balance_sistema = Decimal(request.POST.get('balance_sistema', 0))
        
        diferencia = balance_real - balance_sistema
        
        if diferencia != 0:
            # Ajustamos el saldo inicial del residencial para cuadrar la matemática global
            residencial.saldo_inicial += diferencia
            residencial.save()
            
            if diferencia > 0:
                messages.success(request, f"✅ Banco cuadrado. Se sumaron ${diferencia:,.2f} al sistema.")
            else:
                messages.warning(request, f"✅ Banco cuadrado. Se restaron ${abs(diferencia):,.2f} al sistema.")
        
        # Recargar la misma página con el mismo mes y año
        return redirect(f"{request.path}?mes={mes_seleccionado}&anio={anio_seleccionado}")
    # ----------------------------------------

    # 2. CÁLCULO DE INGRESOS DEL PERIODO
    ingresos_mant = Factura.objects.filter(
        residencial=residencial, tipo='CUOTA', estado='PAGADO', 
        fecha_pago__year=anio_seleccionado, fecha_pago__month=mes_seleccionado
    ).aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')

    ingresos_gas = Factura.objects.filter(
        residencial=residencial, tipo='GAS', estado='PAGADO', 
        fecha_pago__year=anio_seleccionado, fecha_pago__month=mes_seleccionado
    ).aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')

    ingresos_extra = IngresoExtraordinario.objects.filter(
        Apartamento__residencial=residencial, 
        fecha_pago__year=anio_seleccionado, fecha_pago__month=mes_seleccionado
    ).aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')

    total_ingresos_periodo = ingresos_mant + ingresos_gas + ingresos_extra

    # 3. CÁLCULO DE GASTOS DEL PERIODO
    gastos_qs = Gasto.objects.filter(
        residencial=residencial, 
        fecha_gasto__year=anio_seleccionado, fecha_gasto__month=mes_seleccionado
    )
    total_gastos_periodo = gastos_qs.aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')

    balance_del_periodo = total_ingresos_periodo - total_gastos_periodo

    # 4. CÁLCULO DEL BALANCE GLOBAL ESPERADO EN EL BANCO (Hasta este mes)
    import calendar
    ultimo_dia_mes = calendar.monthrange(anio_seleccionado, mes_seleccionado)[1]
    fecha_corte = timezone.datetime(anio_seleccionado, mes_seleccionado, ultimo_dia_mes).date()

    ingresos_historicos = Factura.objects.filter(
        residencial=residencial, estado='PAGADO', fecha_pago__lte=fecha_corte
    ).aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')

    extras_historicos = IngresoExtraordinario.objects.filter(
        Apartamento__residencial=residencial, fecha_pago__lte=fecha_corte
    ).aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')

    gastos_historicos = Gasto.objects.filter(
        residencial=residencial, fecha_gasto__lte=fecha_corte
    ).aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')

    balance_esperado_banco = residencial.saldo_inicial + ingresos_historicos + extras_historicos - gastos_historicos

    # Preparar listas de meses y años para el formulario
    lista_meses = [{'id': i, 'nombre': timezone.datetime(2000, i, 1).strftime('%B').capitalize()} for i in range(1, 13)]
    lista_anios = range(2024, hoy.year + 2)

    context = {
        'mes_seleccionado': mes_seleccionado,
        'anio_seleccionado': anio_seleccionado,
        'lista_meses': lista_meses,
        'lista_anios': lista_anios,
        
        'ingresos_mant': ingresos_mant,
        'ingresos_gas': ingresos_gas,
        'ingresos_extra': ingresos_extra,
        'total_ingresos_periodo': total_ingresos_periodo,
        
        'gastos_detalle': gastos_qs,
        'total_gastos_periodo': total_gastos_periodo,
        
        'balance_del_periodo': balance_del_periodo,
        'balance_esperado_banco': balance_esperado_banco
    }

    return render(request, 'core/reporte_mensual_dinamico.html', context)

@login_required
def reporte_estado_cuenta(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    residencial = request.user.residencial
    
    # Lista de vecinos para el selector
    vecinos = Usuario.objects.filter(residencial=residencial).order_by('apartamento__numero')
    
    vecino_seleccionado = None
    facturas = []
    total_deuda = Decimal('0.00')

    # Si se seleccionó un vecino en el formulario
    usuario_id = request.GET.get('usuario_id')
    if usuario_id:
        vecino_seleccionado = get_object_or_404(Usuario, id=usuario_id, residencial=residencial)
        
        # Traemos todas sus facturas (pagadas y pendientes) ordenadas de la más nueva a la más vieja
        facturas = Factura.objects.filter(usuario=vecino_seleccionado).order_by('-fecha_emision')
        
        # Calculamos la deuda total actual
        deudas = facturas.filter(estado='PENDIENTE')
        total_deuda = sum((f.saldo_pendiente if f.saldo_pendiente is not None else f.monto) for f in deudas)

    context = {
        'vecinos': vecinos,
        'vecino_seleccionado': vecino_seleccionado,
        'facturas': facturas,
        'total_deuda': total_deuda,
        'hoy': timezone.now().date(),
        'residencial': residencial
    }
    
    return render(request, 'core/reporte_estado_cuenta.html', context)

@login_required
def reporte_morosidad(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    residencial = request.user.residencial
    hoy = timezone.now().date()
    
    vecinos = Usuario.objects.filter(residencial=residencial).order_by('apartamento__numero')
    
    datos_morosidad = []
    
    # Acumuladores globales para el final de la tabla
    totales_globales = {
        'al_dia': Decimal('0.00'),   # Debe dinero, pero aún no vence
        'dias_30': Decimal('0.00'),  # 1 a 30 días vencido
        'dias_60': Decimal('0.00'),  # 31 a 60 días vencido
        'dias_90': Decimal('0.00'),  # 61 a 90 días vencido
        'mas_90': Decimal('0.00'),   # Más de 90 días vencido (Crítico)
        'total': Decimal('0.00')
    }
    
    for vecino in vecinos:
        # Buscamos facturas que no estén pagadas
        facturas_pendientes = Factura.objects.filter(usuario=vecino).exclude(estado='PAGADO')
        
        if not facturas_pendientes.exists():
            continue # Si no debe nada, saltamos al siguiente vecino
            
        vecino_data = {
            'usuario': vecino,
            'apto': vecino.apartamento.numero if vecino.apartamento else 'S/A',
            'al_dia': Decimal('0.00'),
            'dias_30': Decimal('0.00'),
            'dias_60': Decimal('0.00'),
            'dias_90': Decimal('0.00'),
            'mas_90': Decimal('0.00'),
            'total': Decimal('0.00')
        }
        
        for f in facturas_pendientes:
            monto_deuda = f.saldo_pendiente if f.saldo_pendiente is not None else f.monto
            if monto_deuda <= 0:
                continue
                
            # Calculamos los días de atraso basados en la fecha de vencimiento
            dias_vencidos = 0
            if f.fecha_vencimiento and f.fecha_vencimiento < hoy:
                dias_vencidos = (hoy - f.fecha_vencimiento).days
                
            # Asignamos el monto a la cubeta correspondiente
            if dias_vencidos <= 0:
                vecino_data['al_dia'] += monto_deuda
                totales_globales['al_dia'] += monto_deuda
            elif dias_vencidos <= 30:
                vecino_data['dias_30'] += monto_deuda
                totales_globales['dias_30'] += monto_deuda
            elif dias_vencidos <= 60:
                vecino_data['dias_60'] += monto_deuda
                totales_globales['dias_60'] += monto_deuda
            elif dias_vencidos <= 90:
                vecino_data['dias_90'] += monto_deuda
                totales_globales['dias_90'] += monto_deuda
            else:
                vecino_data['mas_90'] += monto_deuda
                totales_globales['mas_90'] += monto_deuda
                
            vecino_data['total'] += monto_deuda
            totales_globales['total'] += monto_deuda
        
        # Solo agregamos al vecino al reporte si su deuda total es mayor a 0
        if vecino_data['total'] > 0:
            datos_morosidad.append(vecino_data)
            
    # Ordenar la lista de mayor a menor deuda (los más críticos arriba)
    datos_morosidad = sorted(datos_morosidad, key=lambda x: x['total'], reverse=True)
    
    context = {
        'datos_morosidad': datos_morosidad,
        'totales_globales': totales_globales,
        'hoy': hoy,
        'residencial': residencial
    }
    
    return render(request, 'core/reporte_morosidad.html', context)

@login_required
def reporte_transparencia(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    residencial = request.user.residencial
    hoy = timezone.now()
    mes_seleccionado = int(request.GET.get('mes', hoy.month))
    anio_raw = str(request.GET.get('anio', hoy.year)).replace('\xa0', '').replace(' ', '').replace(',', '')
    anio_seleccionado = int(anio_raw)

    # 1. PROYECCIÓN VS RECAUDACIÓN (Eficiencia de Cobro)
    # Buscamos las cuotas generadas en ESTE mes
    facturas_mes = Factura.objects.filter(
        residencial=residencial,
        tipo='CUOTA',
        fecha_emision__year=anio_seleccionado,
        fecha_emision__month=mes_seleccionado
    )
    
    proyectado = facturas_mes.aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')
    
    # Calculamos cuánto de ese monto facturado ya entró al banco
    recaudado = sum(f.monto - (f.saldo_pendiente if f.saldo_pendiente is not None else f.monto) for f in facturas_mes if f.estado == 'PENDIENTE')
    recaudado += sum(f.monto for f in facturas_mes if f.estado == 'PAGADO')

    eficiencia = 0
    if proyectado > 0:
        eficiencia = (recaudado / proyectado) * 100

    # 2. GASTOS POR CATEGORÍA
    gastos_mes = Gasto.objects.filter(
        residencial=residencial,
        fecha_gasto__year=anio_seleccionado,
        fecha_gasto__month=mes_seleccionado
    )
    total_gastos = gastos_mes.aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')

    # Agrupamos los gastos por categoría en la base de datos
    gastos_por_categoria = gastos_mes.values('categoria').annotate(total=Sum('monto')).order_by('-total')
    
    # --- CORRECCIÓN AQUÍ: Usamos TUS categorías exactas ---
    cat_dict = dict(Gasto.CATEGORIAS)
    
    # Preparamos las listas para el gráfico de Chart.js
    chart_labels = []
    chart_data = []
    lista_gastos_tabla = []
    
    for g in gastos_por_categoria:
        # Busca el nombre bonito ('Compra de Gas (Camión)') basado en el código ('GAS')
        nombre = cat_dict.get(g['categoria'], g['categoria'])
        total_cat = float(g['total'])
        porcentaje = (total_cat / float(total_gastos) * 100) if total_gastos > 0 else 0
        
        chart_labels.append(nombre)
        chart_data.append(total_cat)
        lista_gastos_tabla.append({
            'nombre': nombre,
            'monto': total_cat,
            'porcentaje': porcentaje
        })

    lista_meses = [{'id': i, 'nombre': timezone.datetime(2000, i, 1).strftime('%B').capitalize()} for i in range(1, 13)]
    lista_anios = range(2024, hoy.year + 2)

    context = {
        'mes_seleccionado': mes_seleccionado,
        'anio_seleccionado': anio_seleccionado,
        'lista_meses': lista_meses,
        'lista_anios': lista_anios,
        
        'proyectado': proyectado,
        'recaudado': float(recaudado),
        'eficiencia': float(eficiencia),
        
        'total_gastos': total_gastos,
        'lista_gastos_tabla': lista_gastos_tabla,
        'chart_labels': json.dumps(chart_labels),
        'chart_data': json.dumps(chart_data),
        'residencial': residencial
    }
    
    return render(request, 'core/reporte_transparencia.html', context)


def landing_page(request):
    # Si el usuario ya inició sesión y entra a la página principal, 
    # es mejor mandarlo directo a su panel de control.
    if request.user.is_authenticated:
        return redirect('dashboard')
        
    return render(request, 'core/landing.html')