from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from core.models import Residencial, Apartamento, Factura, Bitacora

class Command(BaseCommand):
    help = 'Robot Cobrador: Generación de Cuotas y Aplicación de Moras Automáticas'

    def handle(self, *args, **kwargs):
        hoy = timezone.now().date()
        mes_actual = hoy.month
        anio_actual = hoy.year
        
        self.stdout.write(self.style.SUCCESS(f'=== Iniciando Robot Cobrador: {hoy} ==='))
        
        residenciales = Residencial.objects.all()
        
        for residencial in residenciales:
            self.stdout.write(f'\nProcesando Residencial: {residencial.nombre}')
            
            # ---------------------------------------------------------
            # FASE A: GENERACIÓN DE CUOTAS
            # ---------------------------------------------------------
            if hoy.day == residencial.dia_corte:
                self.stdout.write(f'  > Hoy es el día de corte ({residencial.dia_corte}). Generando cuotas...')
                self._generar_cuotas_masivas(residencial, mes_actual, anio_actual, hoy)
            else:
                self.stdout.write(f'  > Hoy NO es el día de corte (Día corte: {residencial.dia_corte}). Saltando generación.')

            # ---------------------------------------------------------
            # FASE B: APLICACIÓN DE MORAS AUTOMÁTICAS
            # ---------------------------------------------------------
            self.stdout.write(f'  > Revisando moras pendientes...')
            self._aplicar_moras_masivas(residencial, hoy)
            
        self.stdout.write(self.style.SUCCESS('\n=== Robot Cobrador Finalizó con Éxito ==='))

    def _generar_cuotas_masivas(self, residencial, mes_actual, anio_actual, hoy):
        apartamentos = Apartamento.objects.filter(residencial=residencial, monto_cuota__gt=0)
        contador = 0
        
        with transaction.atomic():
            for apto in apartamentos:
                dueno = apto.habitantes.first()
                if not dueno:
                    continue
                    
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
                        fecha_vencimiento=hoy + timedelta(days=residencial.dias_gracia),
                        estado='PENDIENTE',
                        saldo_pendiente=apto.monto_cuota
                    )
                    
                    if dueno.saldo_favor_mantenimiento > 0:
                        if dueno.saldo_favor_mantenimiento >= nueva_factura.monto:
                            dueno.saldo_favor_mantenimiento -= nueva_factura.monto
                            nueva_factura.monto_pagado = nueva_factura.monto
                            nueva_factura.saldo_pendiente = 0
                            nueva_factura.estado = 'PAGADO'
                            nueva_factura.fecha_pago = hoy
                        else:
                            abono = dueno.saldo_favor_mantenimiento
                            dueno.saldo_favor_mantenimiento = 0
                            nueva_factura.monto_pagado = abono
                            nueva_factura.saldo_pendiente = nueva_factura.monto - abono
                            
                        dueno.save()
                        nueva_factura.save()
                        
                    contador += 1
            
            # FASE C: AUDITORÍA (BITÁCORA)
            if contador > 0:
                Bitacora.objects.create(
                    residencial=residencial,
                    usuario=None,
                    modulo='FINANZAS/ROBOT',
                    accion=f"El Sistema (Robot Cobrador) generó {contador} cuotas de mantenimiento para el mes.",
                    nivel='INFO'
                )
                self.stdout.write(self.style.SUCCESS(f'    - Se generaron {contador} cuotas exitosamente.'))
            else:
                self.stdout.write('    - No hubo cuotas nuevas por generar.')

    def _aplicar_moras_masivas(self, residencial, hoy):
        porcentaje_str = str(residencial.porcentaje_mora or 0)
        porcentaje = Decimal(porcentaje_str)
        
        if porcentaje <= 0:
            self.stdout.write('    - El residencial no tiene configurado un porcentaje de mora.')
            return

        with transaction.atomic():
            facturas_pendientes = Factura.objects.filter(
                residencial=residencial,
                tipo='CUOTA',
                estado='PENDIENTE',
                fecha_vencimiento__lt=hoy
            )
            
            contador_aplicadas = 0
            
            for factura in facturas_pendientes:
                aplicar = False
                
                if factura.fecha_ultima_mora is None:
                    aplicar = True
                else:
                    if factura.fecha_ultima_mora.month != hoy.month or factura.fecha_ultima_mora.year != hoy.year:
                        dias_pasados = (hoy - factura.fecha_ultima_mora).days
                        if dias_pasados >= 20: 
                            aplicar = True
                            
                if aplicar:
                    recargo = factura.saldo_pendiente * (porcentaje / Decimal('100'))
                    
                    factura.monto += recargo
                    factura.saldo_pendiente += recargo
                    factura.concepto += f" (+{porcentaje}% Mora)"
                    factura.fecha_ultima_mora = hoy 
                    
                    factura.save()
                    contador_aplicadas += 1
                    
            # FASE C: AUDITORÍA (BITÁCORA)
            if contador_aplicadas > 0:
                Bitacora.objects.create(
                    residencial=residencial,
                    usuario=None,
                    modulo='FINANZAS/ROBOT',
                    accion=f"El Sistema (Robot Cobrador) aplicó mora automáticamente a {contador_aplicadas} cuotas vencidas.",
                    nivel='WARNING'
                )
                self.stdout.write(self.style.WARNING(f'    - Se aplicó mora a {contador_aplicadas} cuotas vencidas.'))
            else:
                self.stdout.write('    - No se encontraron moras pendientes por aplicar.')
