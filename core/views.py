from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages 
from django.core.exceptions import ValidationError
import json 
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.utils import timezone   
from datetime import datetime, timedelta 

# --- IMPORTS PARA CORREO (OBLIGATORIOS) ---
from django.core.mail import send_mail
from django.conf import settings
# ------------------------------------------

from .utils import enviar_whatsapp 
from .models import Residencial, Reserva, Apartamento, Usuario, BloqueoFecha, Factura, LecturaGas, Gasto, Aviso, Incidencia
from .forms import ReservaForm, LecturaGasForm, GastoForm, AvisoForm, RegistroVecinoForm, IncidenciaForm
from django.db.models import Sum, Max

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
        context['total_pendiente'] = sum(f.monto for f in mis_facturas if f.estado == 'PENDIENTE')

    else:
        context['mensaje'] = "Usuario sin residencial asignado."

    # 3. LÓGICA DE NOTIFICACIONES
    whatsapp_data = request.session.pop('whatsapp_data', None)
    if whatsapp_data:
        context['whatsapp_data'] = whatsapp_data

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
    
    mensaje_texto = ""
    if accion == 'aprobar':
        reserva.estado = 'APROBADA'
        messages.success(request, f'Reserva aprobada correctamente.')
        mensaje_texto = f"Hola {usuario.first_name}, tu solicitud para el {reserva.area_social.nombre} el día {reserva.fecha_solicitud} ha sido APROBADA ✅. ¡Disfrútalo!"
        
    elif accion == 'rechazar':
        reserva.estado = 'RECHAZADA'
        messages.warning(request, f'Reserva rechazada.')
        mensaje_texto = f"Hola {usuario.first_name}, lamentamos informarte que tu solicitud para el {reserva.area_social.nombre} ha sido RECHAZADA ❌. Contacta a la administración."

    reserva.save()

    if usuario.telefono:
        request.session['whatsapp_data'] = {
            'telefono': usuario.telefono,
            'mensaje': mensaje_texto
        }

    return redirect('dashboard')

@login_required
def obtener_eventos_calendario(request):
    residencial = request.user.residencial
    eventos = []

    bloqueos = BloqueoFecha.objects.filter(residencial=residencial)
    for b in bloqueos:
        eventos.append({
            'title': f"⛔ {b.motivo}",
            'start': b.fecha.strftime("%Y-%m-%d"),
            'display': 'background',
            'color': '#000000',
            'allDay': True
        })

    reservas = Reserva.objects.filter(residencial=residencial, estado='APROBADA')
    
    for r in reservas:
        titulo = "Reservado"
        color = "#dc3545"
        
        if request.user.rol in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
            titulo = f"{r.area_social.nombre} - {r.usuario.username}"
        elif r.usuario == request.user:
            titulo = f"Mi Reserva: {r.area_social.nombre}"
            color = "#198754"

        start_str = r.fecha_solicitud.strftime("%Y-%m-%d")
        
        if r.hora_inicio and r.hora_fin:
             start_iso = datetime.combine(r.fecha_solicitud, r.hora_inicio).isoformat()
             end_iso = datetime.combine(r.fecha_solicitud, r.hora_fin).isoformat()
             
             eventos.append({
                'title': titulo,
                'start': start_iso,
                'end': end_iso,
                'color': color,
                'allDay': False
             })
        else:
             eventos.append({
                'title': titulo,
                'start': start_str,
                'color': color,
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
# VISTA: Registrar Lectura Gas + Email (MODO DIAGNÓSTICO)
# ---------------------------------------------
@login_required
def registrar_lectura_gas(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        messages.error(request, "No tienes permiso.")
        return redirect('dashboard')

    # 1. PROCESAR FORMULARIO (GUARDAR NUEVA LECTURA)
    if request.method == 'POST':
        form = LecturaGasForm(request.user, request.POST)
        if form.is_valid():
            apartamento = form.cleaned_data['apartamento']
            mes_actual = timezone.now().month
            anio_actual = timezone.now().year
            
            # Validar duplicados del mes
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
                
                # Validación de lógica
                if lectura.lectura_actual < lectura.lectura_anterior:
                    messages.error(request, "⛔ Error: La lectura actual es menor a la anterior.")
                else:
                    lectura.save() 
                    
                    # Generar Factura
                    residente = lectura.apartamento.habitantes.first()
                    if residente:
                        consumo = lectura.lectura_actual - lectura.lectura_anterior
                        nueva_factura = Factura.objects.create(
                            residencial=request.user.residencial,
                            usuario=residente,
                            tipo='GAS',
                            concepto=f"Gas: {lectura.lectura_anterior} -> {lectura.lectura_actual} ({consumo:.2f} gls)",
                            monto=lectura.total_a_pagar,
                            fecha_vencimiento=timezone.now().date() + timedelta(days=15),
                            estado='PENDIENTE'
                        )
                        lectura.factura_generada = nueva_factura
                        lectura.save()
                        
                        # --- DIAGNÓSTICO DE CORREO (MODO AGRESIVO) ---
                        if residente.email:
                            print(f"📨 INTENTANDO ENVIAR CORREO A: {residente.email}") 
                            
                            asunto = f"Factura Gas - {request.user.residencial.nombre}"
                            mensaje = f"""
                            Hola {residente.first_name},

                            Se ha generado tu factura de consumo de GAS.

                            ---------------------------------------
                            Lectura Anterior: {lectura.lectura_anterior}
                            Lectura Actual:   {lectura.lectura_actual}
                            Consumo:          {lectura.consumo_galones:.2f} galones
                            ---------------------------------------
                            TOTAL A PAGAR:    ${lectura.total_a_pagar}
                            Vencimiento:      {nueva_factura.fecha_vencimiento}
                            ---------------------------------------
                            """
                            
                            # SIN try/except y con fail_silently=False para provocar el error si falla
                            send_mail(asunto, mensaje, settings.EMAIL_HOST_USER, [residente.email], fail_silently=False)
                            
                        else:
                            print(f"⚠️ EL USUARIO {residente.username} NO TIENE CORREO REGISTRADO")
                        # -------------------------------------------

                        messages.success(request, f"✅ Factura generada para {apartamento.numero}: ${lectura.total_a_pagar}")
                    else:
                        messages.warning(request, f"⚠️ Lectura guardada, pero el apto {apartamento.numero} no tiene dueño asignado.")
                
            return redirect('registrar_lectura_gas')
    else:
        # Pre-llenar precio con el último usado en el edificio
        ultima_general = LecturaGas.objects.filter(residencial=request.user.residencial).last()
        precio = ultima_general.precio_galon_mes if ultima_general else 0.00
        form = LecturaGasForm(request.user, initial={'precio_galon_mes': precio})

    # 2. PREPARAR LA TABLA DE "ESTADO DE MEDIDORES"
    apartamentos = Apartamento.objects.filter(residencial=request.user.residencial).order_by('numero')
    estado_medidores = []

    for apt in apartamentos:
        ultima = LecturaGas.objects.filter(apartamento=apt).order_by('-fecha_lectura').first()
        datos = {
            'apto': apt.numero,
            'ultima_fecha': ultima.fecha_lectura if ultima else "---",
            'lectura_anterior': ultima.lectura_anterior if ultima else 0.0,
            'lectura_actual': ultima.lectura_actual if ultima else 0.0,
            'consumo': (ultima.lectura_actual - ultima.lectura_anterior) if ultima else 0.0,
            'precio': ultima.precio_galon_mes if ultima else 0.0,
            'total': ultima.total_a_pagar if ultima else 0.0,
        }
        estado_medidores.append(datos)

    return render(request, 'core/registrar_gas.html', {
        'form': form,
        'estado_medidores': estado_medidores
    })

# ---------------------------------------------
# VISTA: Generar Cuotas Masivas + Email (MODO DIAGNÓSTICO)
# ---------------------------------------------
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
                nueva_factura = Factura.objects.create(
                    residencial=residencial,
                    usuario=dueno,
                    tipo='CUOTA',
                    concepto=f"Mantenimiento {timezone.now().strftime('%B %Y')}",
                    monto=apto.monto_cuota,
                    fecha_vencimiento=timezone.now().date() + timedelta(days=residencial.dias_gracia),
                    estado='PENDIENTE'
                )
                
                # --- DIAGNÓSTICO DE CORREO (MODO AGRESIVO) ---
                if dueno.email:
                    print(f"📨 INTENTANDO ENVIAR MANTENIMIENTO A: {dueno.email}")
                    asunto = f"Mantenimiento {timezone.now().strftime('%B')} - {residencial.nombre}"
                    mensaje = f"""
                    Hola {dueno.first_name},

                    Se ha generado la cuota de mantenimiento del mes.
                    
                    ---------------------------------------
                    Concepto:      {nueva_factura.concepto}
                    TOTAL A PAGAR: ${nueva_factura.monto}
                    Vencimiento:   {nueva_factura.fecha_vencimiento}
                    ---------------------------------------
                    """
                    # SIN try/except y con fail_silently=False
                    send_mail(asunto, mensaje, settings.EMAIL_HOST_USER, [dueno.email], fail_silently=False)
                else:
                    print(f"⚠️ EL USUARIO {dueno.username} NO TIENE EMAIL, SE SALTA EL ENVÍO.")
                # -------------------------------------------
                
                contador += 1
    
    if contador > 0:
        messages.success(request, f"✅ Se generaron y enviaron {contador} facturas de mantenimiento.")
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

@login_required
def registrar_pago(request, factura_id):
    factura = get_object_or_404(Factura, pk=factura_id, residencial=request.user.residencial)
    
    if request.method == 'POST':
        factura.estado = 'PAGADO'
        factura.fecha_pago = timezone.now().date()
        factura.save()
        messages.success(request, f"💰 Cobro exitoso. Factura de {factura.usuario.username} pagada.")
        
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

    hoy = timezone.now()
    mes_actual = hoy.month
    anio_actual = hoy.year

    ingresos_query = Factura.objects.filter(
        residencial=request.user.residencial,
        estado='PAGADO',
        fecha_pago__month=mes_actual,
        fecha_pago__year=anio_actual
    )
    total_ingresos = ingresos_query.aggregate(Sum('monto'))['monto__sum'] or 0

    gastos_query = Gasto.objects.filter(
        residencial=request.user.residencial,
        fecha_gasto__month=mes_actual,
        fecha_gasto__year=anio_actual
    )
    total_gastos = gastos_query.aggregate(Sum('monto'))['monto__sum'] or 0

    balance = total_ingresos - total_gastos

    return render(request, 'core/reporte_financiero.html', {
        'fecha': hoy,
        'total_ingresos': total_ingresos,
        'total_gastos': total_gastos,
        'balance': balance,
        'lista_ingresos': ingresos_query.order_by('-fecha_pago'),
        'lista_gastos': gastos_query.order_by('-fecha_gasto')
    })

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
@login_required
def lista_vecinos(request):
    if request.user.rol not in ['ADMIN_RESIDENCIAL', 'SUPERADMIN']:
        return redirect('dashboard')
    
    # Buscamos usuarios que pertenezcan a este residencial
    vecinos = Usuario.objects.filter(residencial=request.user.residencial).order_by('apartamento__numero')
    
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

    # --- core/views.py (AL FINAL DEL ARCHIVO) ---

from django.http import HttpResponse


# --- core/views.py (Reemplaza la función test_email_view) ---

def test_email_view(request):
    from django.http import HttpResponse
    from django.core.mail import send_mail, get_connection
    from django.conf import settings
    import os
    
    # 1. Datos de Configuración
    user_env = os.environ.get('EMAIL_HOST_USER', 'NO_CONFIGURADO')
    host = settings.EMAIL_HOST
    port = settings.EMAIL_PORT
    
    html = f"""
    <h1>⚡ Prueba Rápida (Timeout 5s)</h1>
    <ul>
        <li><strong>Usuario:</strong> {user_env}</li>
        <li><strong>Servidor:</strong> {host}:{port}</li>
        <li><strong>SSL:</strong> {settings.EMAIL_USE_SSL} / <strong>TLS:</strong> {settings.EMAIL_USE_TLS}</li>
    </ul>
    <hr>
    """
    
    try:
        # 2. EL TRUCO: Creamos una conexión manual con solo 5 SEGUNDOS de paciencia
        connection = get_connection(
            backend=settings.EMAIL_BACKEND,
            timeout=30  # <--- ESTO EVITA LA PANTALLA BLANCA ETERNA
        )
        
        send_mail(
            'Prueba Anti-Timeout',
            'Si lees esto, logramos vencer el bloqueo.',
            user_env,
            ['jreynoso280988@gmail.com'], # <--- ¡PON TU EMAIL AQUÍ!
            connection=connection, 
            fail_silently=False
        )
        return HttpResponse(html + "<h2 style='color:green'>✅ ¡ÉXITO! Enviado.</h2>")

    except Exception as e:
        # 3. Si falla, mostramos el error EXACTO
        return HttpResponse(html + f"""
            <h2 style='color:red'>❌ ERROR CAPTURADO</h2>
            <p>Django dice:</p>
            <pre style='background:#f8d7da; padding:15px; border:2px solid red; font-size:16px;'>{e}</pre>
            <p><strong>¿Qué hacer?</strong></p>
            <ul>
                <li>Si dice <strong>TimeoutError</strong>: Google está bloqueando la IP de Railway.</li>
                <li>Si dice <strong>AuthenticationError</strong>: La contraseña en Railway está mal.</li>
            </ul>
        """)