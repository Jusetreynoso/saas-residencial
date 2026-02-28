from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
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
from django.contrib.auth.forms import SetPasswordForm 
from .forms import (
    ReservaForm, 
    LecturaGasForm, 
    GastoForm, 
    AvisoForm, 
    RegistroVecinoForm, 
    IncidenciaForm,
    EditarVecinoForm,
    AbonoForm,
    ReportePagoForm
)

from .models import Residencial, Reserva, Apartamento, Usuario, BloqueoFecha, Factura, LecturaGas, Gasto, Aviso, Incidencia, ReportePago
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
        
        if user.rol in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
            context['solicitudes_pendientes'] = Reserva.objects.filter(
                residencial=user.residencial, 
                estado='PENDIENTE'
            ).order_by('fecha_solicitud')

        if user.apartamento:
            context['mi_apartamento'] = user.apartamento
        
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
    reserva = get_object_or_404(Reserva, pk=reserva_id, usuario=request.user)
    
    if reserva.estado in ['PENDIENTE', 'APROBADA']:
        fecha = reserva.fecha_solicitud
        area = reserva.area_social.nombre
        reserva.delete()
        messages.success(request, f'La reserva del {area} para el {fecha} ha sido cancelada correctamente.')
    else:
        messages.error(request, 'No se puede cancelar esta reserva (ya fue rechazada o finalizada).')
        
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
                
                # 2. LÓGICA AUTOMÁTICA DE SALDO A FAVOR
                if dueno.saldo_a_favor > 0:
                    # CASO A: El saldo cubre toda la factura (Ej: Tiene 5000, factura 3000)
                    if dueno.saldo_a_favor >= nueva_factura.monto:
                        dueno.saldo_a_favor -= nueva_factura.monto
                        nueva_factura.monto_pagado = nueva_factura.monto
                        nueva_factura.saldo_pendiente = 0
                        nueva_factura.estado = 'PAGADO'
                        nueva_factura.fecha_pago = timezone.now().date()
                        
                    # CASO B: El saldo es menor a la factura (Ej: Tiene 100, factura 3000)
                    else:
                        abono = dueno.saldo_a_favor
                        dueno.saldo_a_favor = 0 # Se gastó todo su saldo
                        nueva_factura.monto_pagado = abono
                        nueva_factura.saldo_pendiente = nueva_factura.monto - abono
                        # Sigue en estado PENDIENTE, pero con menos deuda
                    
                    # Guardamos los cambios
                    dueno.save()
                    nueva_factura.save()

                contador += 1
    
    if contador > 0:
        messages.success(request, f"✅ Se generaron {contador} facturas (aplicando saldos a favor automáticamente).")
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

        deuda_actual = factura.monto - (factura.monto_pagado or 0)
        
        # 1. Registramos el pago en la factura
        factura.monto_pagado = (factura.monto_pagado or 0) + monto_recibido
        factura.fecha_pago = timezone.now().date()

        # CASO A: Pagó la deuda completa o de más
        if monto_recibido >= deuda_actual:
            factura.estado = 'PAGADO'
            factura.saldo_pendiente = 0
            
            sobrante = monto_recibido - deuda_actual
            
            if sobrante > 0:
                vecino = factura.usuario
                
                # --- CORRECCIÓN DE SEGURIDAD ---
                # Si el campo está vacío (None), lo convertimos a 0 antes de sumar
                saldo_actual = vecino.saldo_a_favor if vecino.saldo_a_favor is not None else Decimal(0)
                vecino.saldo_a_favor = saldo_actual + sobrante
                # -------------------------------
                
                vecino.save() # Guardamos en la Base de Datos
                
                messages.success(request, f"✅ Pagado. Se abonaron ${sobrante:,.2f} al saldo a favor de {vecino.first_name}.")
            else:
                messages.success(request, f"✅ Factura pagada correctamente (Exacto).")

        # CASO B: Pago Parcial (Abono)
        else:
            factura.saldo_pendiente = deuda_actual - monto_recibido
            # IMPORTANTE: Si es pago parcial, el estado sigue siendo PENDIENTE
            # (Opcional: podrías ponerle un estado 'PARCIAL' si quisieras)
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

    # ---------------------------------------------------------
    # 1. TOTALES ANUALES (Tarjetas Superiores - KPIs)
    # ---------------------------------------------------------
    total_ingresos = Factura.objects.filter(
        residencial=residencial, 
        estado='PAGADO', 
        fecha_pago__year=anio_actual
    ).aggregate(Sum('monto'))['monto__sum'] or 0

    total_gastos = Gasto.objects.filter(
        residencial=residencial, 
        fecha_gasto__year=anio_actual
    ).aggregate(Sum('monto'))['monto__sum'] or 0

    balance = total_ingresos - total_gastos

    # ---------------------------------------------------------
    # 2. DATOS PARA GRÁFICOS (Barras y Pastel) - ¡RECUPERADOS!
    # ---------------------------------------------------------
    
    # A. Gráfico de Barras (Evolución Mensual)
    ingresos_qs = Factura.objects.filter(
        residencial=residencial,
        estado='PAGADO',
        fecha_pago__year=anio_actual
    ).annotate(mes=TruncMonth('fecha_pago')).values('mes').annotate(total=Sum('monto')).order_by('mes')

    gastos_qs = Gasto.objects.filter(
        residencial=residencial,
        fecha_gasto__year=anio_actual
    ).annotate(mes=TruncMonth('fecha_gasto')).values('mes').annotate(total=Sum('monto')).order_by('mes')

    datos_por_mes = {}
    
    # Procesar Ingresos
    for i in ingresos_qs:
        mes_str = i['mes'].strftime('%B') 
        if mes_str not in datos_por_mes: datos_por_mes[mes_str] = {'ingreso': 0, 'gasto': 0}
        datos_por_mes[mes_str]['ingreso'] = float(i['total'])

    # Procesar Gastos
    for g in gastos_qs:
        mes_str = g['mes'].strftime('%B')
        if mes_str not in datos_por_mes: datos_por_mes[mes_str] = {'ingreso': 0, 'gasto': 0}
        datos_por_mes[mes_str]['gasto'] = float(g['total'])

    # Listas finales para Chart.js
    bar_labels = list(datos_por_mes.keys())
    bar_ingresos = [d['ingreso'] for d in datos_por_mes.values()]
    bar_gastos = [d['gasto'] for d in datos_por_mes.values()]

    # B. Gráfico de Pastel (Fuente de Ingresos)
    ingresos_tipo = Factura.objects.filter(
        residencial=residencial,
        estado='PAGADO',
        fecha_pago__year=anio_actual
    ).values('tipo').annotate(total=Sum('monto'))

    pie_labels = [item['tipo'] for item in ingresos_tipo]
    pie_data = [float(item['total']) for item in ingresos_tipo]


    # ---------------------------------------------------------
    # 3. DATOS PARA LIBRO DIARIO (Tabla Excel)
    # ---------------------------------------------------------
    
    # A. Saldo Histórico (Todo lo anterior al mes actual)
    ingresos_historicos = Factura.objects.filter(
        residencial=residencial, 
        estado='PAGADO', 
        fecha_pago__lt=timezone.datetime(anio_actual, mes_actual, 1)
    ).aggregate(Sum('monto'))['monto__sum'] or 0
    
    gastos_historicos = Gasto.objects.filter(
        residencial=residencial, 
        fecha_gasto__lt=timezone.datetime(anio_actual, mes_actual, 1)
    ).aggregate(Sum('monto'))['monto__sum'] or 0

    # Fórmula: Saldo Inicial Configurado + Ingresos Viejos - Gastos Viejos
    saldo_acumulado = residencial.saldo_inicial + ingresos_historicos - gastos_historicos
    saldo_inicial_mes = saldo_acumulado 

    # B. Movimientos del Mes Actual
    mov_ingresos = Factura.objects.filter(
        residencial=residencial, 
        estado='PAGADO', 
        fecha_pago__year=anio_actual, 
        fecha_pago__month=mes_actual
    )

    mov_gastos = Gasto.objects.filter(
        residencial=residencial, 
        fecha_gasto__year=anio_actual, 
        fecha_gasto__month=mes_actual
    )

    # C. Unir y Ordenar
    for i in mov_ingresos: 
        i.tipo_mov = 'INGRESO'
        i.fecha_mov = i.fecha_pago
        
    for g in mov_gastos: 
        g.tipo_mov = 'GASTO'
        g.fecha_mov = g.fecha_gasto

    lista_movimientos = sorted(
        chain(mov_ingresos, mov_gastos), 
        key=attrgetter('fecha_mov')
    )

    # D. Calcular tabla línea por línea
    tabla_movimientos = []
    for mov in lista_movimientos:
        if mov.tipo_mov == 'INGRESO':
            saldo_acumulado += mov.monto
        else: # GASTO
            saldo_acumulado -= mov.monto
        
        tabla_movimientos.append({
            'fecha': mov.fecha_mov,
            'concepto': mov.concepto if mov.tipo_mov == 'INGRESO' else mov.descripcion,
            'tipo': mov.tipo_mov,
            'monto': mov.monto,
            'saldo': saldo_acumulado,
            'usuario': mov.usuario.username if hasattr(mov, 'usuario') and mov.usuario else 'Admin'
        })

    # ---------------------------------------------------------
    # 4. CONTEXTO FINAL (Empaquetar todo para el HTML)
    # ---------------------------------------------------------
    context = {
        'anio': anio_actual,
        'mes_nombre': timezone.now().strftime('%B'),
        'total_ingresos': total_ingresos,
        'total_gastos': total_gastos,
        'balance': balance,
        
        # Datos para Gráficas (JSON Strings)
        'bar_labels': json.dumps(bar_labels),
        'bar_ingresos': json.dumps(bar_ingresos),
        'bar_gastos': json.dumps(bar_gastos),
        'pie_labels': json.dumps(pie_labels),
        'pie_data': json.dumps(pie_data),
        
        # Datos para Tabla Libro Diario
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


# --- GESTIÓN DE VECINOS ---
# En core/views.py

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

@login_required
def aplicar_moras(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    residencial = request.user.residencial
    hoy = timezone.now().date()
    porcentaje = residencial.porcentaje_mora or 0
    
    if porcentaje <= 0:
        messages.warning(request, "⚠️ No tienes configurado el porcentaje de mora en el Residencial.")
        return redirect('dashboard')

    # 1. Buscamos SOLO facturas de MANTENIMIENTO que se deban
    facturas_pendientes = Factura.objects.filter(
        residencial=residencial,
        tipo='CUOTA',           # <--- OJO: Solo aplica a cuotas, no a Gas ni otros
        estado='PENDIENTE',     # Que no estén pagadas
        fecha_vencimiento__lt=hoy # Que ya hayan vencido
    )
    
    contador = 0

    for factura in facturas_pendientes:
        aplicar = False
        
        # CASO 1: Nunca se le ha cobrado mora (es la primera vez)
        if factura.fecha_ultima_mora is None:
            aplicar = True
            
        # CASO 2: Ya se le cobró, pero verificamos si pasaron 30 días desde la última vez
        else:
            dias_pasados = (hoy - factura.fecha_ultima_mora).days
            if dias_pasados >= 30:
                aplicar = True

        # --- APLICAMOS EL CASTIGO ---
        if aplicar:
            # Calculamos la mora sobre el SALDO PENDIENTE (lo justo) o sobre el MONTO ORIGINAL
            # Aquí uso saldo_pendiente para que sea interés sobre deuda actual.
            recargo = factura.saldo_pendiente * (porcentaje / 100)
            
            # Actualizamos valores
            factura.monto += recargo
            factura.saldo_pendiente += recargo
            factura.concepto += f" (+{porcentaje}%)" # Agregamos marca al texto
            
            # IMPORTANTE: Guardamos la fecha de HOY para que no le vuelva a cobrar hasta dentro de 30 días
            factura.fecha_ultima_mora = hoy 
            
            factura.save()
            contador += 1

    if contador > 0:
        messages.success(request, f"✅ Se aplicó mora acumulativa a {contador} facturas de mantenimiento.")
    else:
        messages.info(request, "ℹ️ No hay facturas que cumplan el ciclo de mora hoy (o ya se les aplicó este mes).")

    return redirect('dashboard')

@login_required
def registrar_abono(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')

    if request.method == 'POST':
        form = AbonoForm(request.user, request.POST)
        if form.is_valid():
            vecino = form.cleaned_data['usuario']
            monto = form.cleaned_data['monto']
            concepto = form.cleaned_data['concepto']

            # 1. Aumentamos el Saldo a Favor REAL del vecino
            # (Usamos 'or 0' por seguridad si el campo está vacío)
            saldo_actual = vecino.saldo_a_favor if vecino.saldo_a_favor else Decimal(0)
            vecino.saldo_a_favor = saldo_actual + monto
            vecino.save()

            # 2. Creamos un registro "Factura Pagada" para el historial
            # Esto sirve para que el vecino vea en su estado de cuenta que pagó ese dinero
            Factura.objects.create(
                residencial=request.user.residencial,
                usuario=vecino,
                tipo='OTRO', # Usamos 'OTRO' para diferenciarlo de cuotas normales
                concepto=f"🟢 ABONO: {concepto}",
                monto=monto,
                monto_pagado=monto,
                estado='PAGADO', # Nace pagada
                fecha_emision=timezone.now().date(),
                fecha_vencimiento=timezone.now().date(),
                fecha_pago=timezone.now().date(),
                saldo_pendiente=0
            )

            messages.success(request, f"✅ Abono de ${monto} registrado exitosamente para {vecino.first_name}. Nuevo saldo a favor: ${vecino.saldo_a_favor}")
            return redirect('cuentas_por_cobrar')
    else:
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
    
    total_deuda_global = 0
    total_saldo_favor_global = 0

    for apt in apartamentos:
        dueno = apt.habitantes.first()
        
        deuda = 0
        saldo_favor = 0
        nombre_dueno = "--- Sin Asignar ---"
        
        if dueno:
            nombre_dueno = f"{dueno.first_name} {dueno.last_name}"
            saldo_favor = dueno.saldo_a_favor or 0
            
            # Calculamos deuda real
            facturas_pendientes = Factura.objects.filter(usuario=dueno, estado='PENDIENTE')
            deuda = sum((f.saldo_pendiente or f.monto) for f in facturas_pendientes)

        data_financiera.append({
            'apto': apt.numero,
            'dueno': nombre_dueno,
            'deuda': deuda,
            'saldo_favor': saldo_favor,
            'estado': 'Moroso' if deuda > 0 else 'Al día'
        })
        
        total_deuda_global += deuda
        total_saldo_favor_global += saldo_favor

    return render(request, 'core/balance_residencial.html', {
        'data': data_financiera,
        'total_deuda': total_deuda_global,
        'total_saldo': total_saldo_favor_global
    })