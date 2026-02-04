# core/utils.py
from django.core.mail import send_mail
from django.conf import settings

def enviar_whatsapp(nombre_usuario, telefono, mensaje):
    """
    Función centralizada para enviar mensajes.
    Por ahora SIMULA el envío imprimiendo en la consola negra.
    """
    
    # 1. Validación básica
    if not telefono:
        print(f"⚠️ ERROR DE NOTIFICACIÓN: El usuario {nombre_usuario} no tiene teléfono registrado.")
        return False

    # 2. Aquí iría la conexión real con Twilio o Meta en el futuro.
    # Por ahora, simulamos el envío:
    
    print("\n" + "="*50)
    print(f"📱 [WHATSAPP SALIENTE]")
    print(f"👤 Para: {nombre_usuario} ({telefono})")
    print(f"💬 Mensaje: {mensaje}")
    print("="*50 + "\n")
    
    return True
from django.core.mail import send_mail
from django.conf import settings

def enviar_correo_factura(factura):
    """
    Envía un correo al residente notificando una nueva factura.
    """
    # 1. Validamos que el usuario tenga correo
    if not factura.usuario.email:
        print(f"⚠️ El usuario {factura.usuario.username} no tiene email configurado.")
        return

    # 2. Preparamos el asunto y el mensaje
    asunto = f"🔔 Nueva Factura Disponible - {factura.residencial.nombre}"
    
    mensaje = f"""
    Hola {factura.usuario.first_name},

    Se ha generado una nueva factura en tu estado de cuenta.

    ------------------------------------------
    🏢 Residencial: {factura.residencial.nombre}
    📋 Concepto:    {factura.concepto}
    💰 Monto:       ${factura.monto}
    📅 Vencimiento: {factura.fecha_vencimiento}
    ------------------------------------------

    Por favor, ingresa a la plataforma para ver el detalle o realizar el pago.
    
    Atentamente,
    La Administración.
    """

    # 3. Enviamos el correo (Django maneja la magia)
    try:
        send_mail(
            asunto,
            mensaje,
            settings.EMAIL_HOST_USER, # Remitente
            [factura.usuario.email],  # Destinatario
            fail_silently=False,
        )
        print(f"✅ Correo enviado a {factura.usuario.email}")
    except Exception as e:
        print(f"❌ Error enviando correo: {e}")