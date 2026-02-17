from django import template

register = template.Library()

@register.filter
def dinero(valor):
    """
    Convierte un número a formato moneda USD/RD:
    - Miles con coma (,)
    - Decimales con punto (.)
    - Negativos entre paréntesis ()
    Ejemplo: 12500.50 -> $12,500.50
    Ejemplo: -500 -> ($500.00)
    """
    try:
        valor = float(valor)
    except (ValueError, TypeError):
        return valor

    # Formateamos con coma para miles y punto para decimales (Estándar Python)
    # abs(valor) quita el signo negativo para ponerlo nosotros con paréntesis
    formato_base = "{:,.2f}".format(abs(valor))

    if valor < 0:
        return f"(${formato_base})"
    else:
        return f"${formato_base}"