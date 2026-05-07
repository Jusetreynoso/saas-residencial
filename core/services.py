from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from .models import Factura, Usuario

def procesar_pago_fifo(usuario: Usuario, monto: Decimal, tipo_pago: str) -> dict:
    """
    Procesa un abono/pago usando el algoritmo FIFO (First In, First Out).
    Aplica el monto a las facturas pendientes más antiguas primero.
    Si sobra dinero, lo guarda en el bolsillo correspondiente del usuario.
    
    Retorna:
        dict: {
            "facturas_pagadas": int,
            "sobrante": Decimal,
            "bolsillo_afectado": str
        }
    """
    with transaction.atomic():
        monto_disponible = Decimal(monto)
        
        # 1. Determinar el tipo de factura a pagar
        filtro_tipo = 'GAS' if tipo_pago == 'GAS' else 'CUOTA'
        
        # 2. Buscar facturas pendientes ordenadas por fecha de vencimiento (más vieja primero)
        facturas_pendientes = Factura.objects.filter(
            usuario=usuario,
            estado='PENDIENTE',
            tipo=filtro_tipo
        ).order_by('fecha_vencimiento')
        
        facturas_pagadas_count = 0
        
        # 3. Algoritmo Mata-Deudas (FIFO)
        for factura in facturas_pendientes:
            if monto_disponible <= 0:
                break
                
            deuda = factura.saldo_pendiente if factura.saldo_pendiente is not None else factura.monto
            
            if monto_disponible >= deuda:
                monto_disponible -= deuda
                factura.saldo_pendiente = 0
                factura.monto_pagado = (factura.monto_pagado or 0) + deuda
                factura.estado = 'PAGADO'
                factura.fecha_pago = timezone.now().date()
                factura.save()
                facturas_pagadas_count += 1
            else:
                factura.saldo_pendiente = deuda - monto_disponible
                factura.monto_pagado = (factura.monto_pagado or 0) + monto_disponible
                monto_disponible = 0
                factura.save()
                
        # 4. Guardar el sobrante en el bolsillo correcto
        bolsillo_nombre = ""
        if monto_disponible > 0:
            if tipo_pago == 'GAS':
                saldo_actual = usuario.saldo_favor_gas or Decimal(0)
                usuario.saldo_favor_gas = saldo_actual + monto_disponible
                bolsillo_nombre = "Gas"
            else:
                saldo_actual = usuario.saldo_favor_mantenimiento or Decimal(0)
                usuario.saldo_favor_mantenimiento = saldo_actual + monto_disponible
                bolsillo_nombre = "Mantenimiento"
            usuario.save()
            
        return {
            "facturas_pagadas": facturas_pagadas_count,
            "sobrante": monto_disponible,
            "bolsillo_afectado": bolsillo_nombre
        }
