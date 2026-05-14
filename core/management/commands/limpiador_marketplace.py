from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import ProductoMarketplace

class Command(BaseCommand):
    help = 'Limpia físicamente las fotos de los anuncios del Marketplace que ya vencieron para ahorrar espacio.'

    def handle(self, *args, **options):
        hoy = timezone.now()
        
        # Buscar productos que estén vencidos o cuya fecha ya haya pasado y aún tengan imagen
        # Usamos exclución para asegurarnos de que la imagen no esté vacía
        productos_vencidos = ProductoMarketplace.objects.filter(
            fecha_expiracion__lt=hoy
        ).exclude(imagen='')

        if not productos_vencidos.exists():
            self.stdout.write(self.style.SUCCESS("✅ No hay imágenes de anuncios vencidos para limpiar."))
            return

        total_limpiados = 0
        for producto in productos_vencidos:
            # 1. Aseguramos que su estado visual sea VENCIDO por si acaso no se actualizó
            if producto.estado != 'VENCIDO':
                producto.estado = 'VENCIDO'
                
            # 2. Borramos físicamente la imagen de Cloudinary
            if producto.imagen:
                self.stdout.write(f"Borrando imagen de: {producto.titulo} (ID: {producto.id})")
                producto.imagen.delete(save=False) # Borra el archivo
                producto.imagen = None             # Limpia el campo en la BD
                
            # 3. Guardamos los cambios
            producto.save()
            total_limpiados += 1

        self.stdout.write(self.style.SUCCESS(f"🚀 Limpieza completada: Se borraron {total_limpiados} imágenes de Cloudinary."))
