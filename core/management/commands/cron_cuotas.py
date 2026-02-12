from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Residencial, Apartamento, Factura
from datetime import timedelta
from decimal import Decimal

class Command(BaseCommand):
    help = 'Genera cuotas de mantenimiento automáticas si hoy es el día de corte'

    def handle(self, *args, **kwargs):
        self.stdout.write("🤖 Iniciando robot de facturación automática...")
        
        hoy = timezone.now().date()
        residenciales = Residencial.objects.all()
        
        total_generadas = 0

        for res in residenciales:
            # Validamos que el residencial tenga configurado el día de corte
            if not res.dia_corte:
                continue

            # LÓGICA DE ACTIVACIÓN:
            # Se activa si hoy es IGUAL o MAYOR al día de corte.
            # (El "Mayor" es por seguridad: si el servidor se apaga el día 15, 
            # el día 16 el robot se da cuenta que no facturó y lo hace).
            if hoy.day >= res.dia_corte:
                
                # REVISAR SI YA EXISTEN FACTURAS DE ESTE MES
                # Para no cobrar doble si el script corre mañana también.
                existe = Factura.objects.filter(
                    residencial=res,
                    tipo='CUOTA',
                    fecha_emision__month=hoy.month,
                    fecha_emision__year=hoy.year
                ).exists()

                if not existe:
                    self.stdout.write(f"⚡ Generando facturas para: {res.nombre} (Corte día {res.dia_corte})")
                    
                    apartamentos = Apartamento.objects.filter(residencial=res, monto_cuota__gt=0)
                    
                    for apto in apartamentos:
                        dueno = apto.habitantes.first()
                        
                        if dueno:
                            # 1. Crear Factura PENDIENTE
                            nueva_factura = Factura.objects.create(
                                residencial=res,
                                usuario=dueno,
                                tipo='CUOTA',
                                concepto=f"Mantenimiento {timezone.now().strftime('%B %Y')}",
                                monto=apto.monto_cuota,
                                fecha_vencimiento=hoy + timedelta(days=res.dias_gracia),
                                estado='PENDIENTE',
                                saldo_pendiente=apto.monto_cuota
                            )
                            
                            # 2. APLICAR SALDO A FAVOR (AUTOMÁTICO)
                            if dueno.saldo_a_favor and dueno.saldo_a_favor > 0:
                                if dueno.saldo_a_favor >= nueva_factura.monto:
                                    # Paga todo
                                    dueno.saldo_a_favor -= nueva_factura.monto
                                    nueva_factura.monto_pagado = nueva_factura.monto
                                    nueva_factura.saldo_pendiente = 0
                                    nueva_factura.estado = 'PAGADO'
                                    nueva_factura.fecha_pago = hoy
                                else:
                                    # Paga parcial
                                    abono = dueno.saldo_a_favor
                                    dueno.saldo_a_favor = 0
                                    nueva_factura.monto_pagado = abono
                                    nueva_factura.saldo_pendiente = nueva_factura.monto - abono
                                
                                dueno.save()
                                nueva_factura.save()
                            
                            total_generadas += 1
                else:
                    self.stdout.write(f"✅ {res.nombre}: Ya tiene facturas de este mes. Saltando.")
            else:
                self.stdout.write(f"⏳ {res.nombre}: Aún no es día de corte (Día {res.dia_corte}).")

        self.stdout.write(f"🏁 Proceso terminado. Facturas generadas hoy: {total_generadas}")