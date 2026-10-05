from django.test import TestCase
from django.contrib.auth import get_user_model
from core.models import Residencial, Categoria, Apartamento
from core.forms import GastoForm, IngresoExtraForm

Usuario = get_user_model()

class CategoriaTestCase(TestCase):
    def setUp(self):
        # 1. Crear residenciales de prueba (esto disparará el signal de inicialización)
        self.res1 = Residencial.objects.create(
            nombre="Torre A",
            direccion="Calle 1",
            dia_corte=1,
            dias_gracia=15,
            porcentaje_mora=5.00
        )
        self.res2 = Residencial.objects.create(
            nombre="Torre B",
            direccion="Calle 2",
            dia_corte=5,
            dias_gracia=10,
            porcentaje_mora=10.00
        )
        
        # Crear usuarios administradores para cada residencial
        self.admin1 = Usuario.objects.create_user(
            username="admin1",
            password="password123",
            rol="ADMIN_RESIDENCIAL",
            residencial=self.res1
        )
        self.admin2 = Usuario.objects.create_user(
            username="admin2",
            password="password123",
            rol="ADMIN_RESIDENCIAL",
            residencial=self.res2
        )
        
        # Crear apartamentos para pruebas de IngresoExtraForm
        self.apto1 = Apartamento.objects.create(residencial=self.res1, numero="101", monto_cuota=1500)
        self.apto2 = Apartamento.objects.create(residencial=self.res2, numero="201", monto_cuota=2000)

    def test_inicializacion_categorias_por_defecto(self):
        """Verifica que el signal post_save cree las categorías por defecto en cada residencial."""
        cats_res1 = Categoria.objects.filter(residencial=self.res1)
        cats_res2 = Categoria.objects.filter(residencial=self.res2)
        
        # Deben haberse creado 17 categorías de gasto y 5 de ingreso = 22 categorías totales por residencial
        self.assertEqual(cats_res1.count(), 22)
        self.assertEqual(cats_res2.count(), 22)
        
        # Verificar algunas categorías de gasto específicas
        self.assertTrue(cats_res1.filter(codigo="NOMINA", tipo="GASTO").exists())
        self.assertTrue(cats_res1.filter(codigo="COMPRAS_GAS", tipo="GASTO").exists())
        
        # Verificar algunas categorías de ingreso específicas
        self.assertTrue(cats_res1.filter(codigo="CONTROL", tipo="INGRESO").exists())
        self.assertTrue(cats_res1.filter(codigo="MULTA", tipo="INGRESO").exists())

    def test_gasto_form_filtra_categorias_activas(self):
        """Verifica que GastoForm cargue dinámicamente y solo las categorías de tipo GASTO activas de ese residencial."""
        # Inicialmente todas están activas. Desactivemos una en el residencial 1
        cat_nomina = Categoria.objects.get(residencial=self.res1, codigo="NOMINA", tipo="GASTO")
        cat_nomina.activo = False
        cat_nomina.save()
        
        # Instanciar el formulario para el administrador del residencial 1
        form1 = GastoForm(user=self.admin1)
        choices_res1 = dict(form1.fields['categoria'].choices)
        
        # 'NOMINA' no debe aparecer en choices de residencial 1
        self.assertNotIn("NOMINA", choices_res1)
        # Pero 'COMPRAS_GAS' (que sigue activa) sí debe aparecer
        self.assertIn("COMPRAS_GAS", choices_res1)
        
        # Instanciar el formulario para el administrador del residencial 2
        form2 = GastoForm(user=self.admin2)
        choices_res2 = dict(form2.fields['categoria'].choices)
        
        # 'NOMINA' sí debe estar en choices de residencial 2 ya que allí sigue activa
        self.assertIn("NOMINA", choices_res2)

    def test_ingreso_extra_form_filtra_categorias_activas(self):
        """Verifica que IngresoExtraForm cargue dinámicamente y solo las categorías de tipo INGRESO activas."""
        # Desactivemos una categoría de ingreso en el residencial 1
        cat_multa = Categoria.objects.get(residencial=self.res1, codigo="MULTA", tipo="INGRESO")
        cat_multa.activo = False
        cat_multa.save()
        
        form1 = IngresoExtraForm(admin_user=self.admin1)
        choices_res1 = dict(form1.fields['categoria'].choices)
        
        # 'MULTA' no debe aparecer en choices del residencial 1
        self.assertNotIn("MULTA", choices_res1)
        self.assertIn("CONTROL", choices_res1)
        
        form2 = IngresoExtraForm(admin_user=self.admin2)
        choices_res2 = dict(form2.fields['categoria'].choices)
        
        # 'MULTA' sí debe estar activo en choices del residencial 2
        self.assertIn("MULTA", choices_res2)

    def test_creacion_categoria_personalizada(self):
        """Verifica que se puedan crear categorías de forma personalizada y sean independientes."""
        # Agregar categoría personalizada en residencial 1
        nueva_cat = Categoria.objects.create(
            residencial=self.res1,
            nombre="Mantenimiento Elevador B",
            codigo="MANTENIMIENTO_ELEVADOR_B",
            tipo="GASTO",
            activo=True
        )
        
        form1 = GastoForm(user=self.admin1)
        choices_res1 = dict(form1.fields['categoria'].choices)
        self.assertIn("MANTENIMIENTO_ELEVADOR_B", choices_res1)
        self.assertEqual(choices_res1["MANTENIMIENTO_ELEVADOR_B"], "Mantenimiento Elevador B")
        
        form2 = GastoForm(user=self.admin2)
        choices_res2 = dict(form2.fields['categoria'].choices)
        self.assertNotIn("MANTENIMIENTO_ELEVADOR_B", choices_res2)

    def test_eliminar_lectura_gas_y_factura(self):
        """Verifica que un administrador pueda eliminar una lectura de gas errónea y su factura asociada."""
        from core.models import LecturaGas, Factura
        from decimal import Decimal

        # Crear residente y asignarle el apto1
        residente = Usuario.objects.create_user(
            username="residente1",
            password="password123",
            rol="RESIDENTE",
            residencial=self.res1,
            apartamento=self.apto1
        )
        
        # Crear factura de gas errónea y su lectura
        factura = Factura.objects.create(
            residencial=self.res1,
            usuario=residente,
            tipo='GAS',
            concepto='Gas Erróneo',
            monto=Decimal('500.00'),
            fecha_vencimiento='2026-12-31',
            saldo_pendiente=Decimal('500.00')
        )
        lectura = LecturaGas.objects.create(
            residencial=self.res1,
            apartamento=self.apto1,
            lectura_anterior=Decimal('10.000'),
            lectura_actual=Decimal('500.000'),
            precio_galon_mes=Decimal('150.00'),
            factura_generada=factura
        )

        # Iniciar sesión como admin1
        self.client.login(username='admin1', password='password123')
        
        # Ejecutar la eliminación
        response = self.client.get(f'/facturacion/gas/eliminar-lectura/{lectura.id}/', follow=True)
        self.assertEqual(response.status_code, 200)
        
        # Verificar que la lectura y factura hayan sido eliminadas
        self.assertFalse(LecturaGas.objects.filter(id=lectura.id).exists())
        self.assertFalse(Factura.objects.filter(id=factura.id).exists())

    def test_estado_cuenta_residente_privacidad(self):
        residente1 = Usuario.objects.create_user(
            username="residente1",
            password="password123",
            rol="RESIDENTE",
            residencial=self.res1
        )
        # Iniciar sesión como residente1
        self.client.login(username='residente1', password='password123')
        
        # Intentar acceder al estado de cuenta pasando el id de otro usuario (admin1)
        response = self.client.get(f'/reportes/estado-cuenta/?usuario_id={self.admin1.id}')
        self.assertEqual(response.status_code, 200)
        
        # Verificar que la respuesta contiene el usuario residente1 y NO admin1
        self.assertEqual(response.context['vecino_seleccionado'], residente1)
        self.assertIsNone(response.context['vecinos'])
