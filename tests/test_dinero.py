import itertools

from hypothesis import given, settings, strategies as st

from till import dinero
from till.dinero import DENOMINACIONES_MXN


def test_mezcla_que_el_voraz_no_encuentra():
    # 60 pesos con 50 y 20: el voraz toma un 50 y se atora; la búsqueda usa 3 de 20.
    assert dinero.calcular_mezcla(dinero.pesos(60), {50: 10, 20: 10}) == {20: 3}


def test_mezcla_prefiere_billetes_grandes():
    m = dinero.calcular_mezcla(dinero.pesos(1700), {1000: 5, 500: 5, 200: 5, 100: 5})
    assert m == {1000: 1, 500: 1, 200: 1}


def test_mezcla_respeta_existencias_y_maximo():
    assert dinero.calcular_mezcla(dinero.pesos(1000), {500: 1}) is None
    assert dinero.calcular_mezcla(dinero.pesos(2000), {20: 1000}, max_billetes=40) is None
    assert dinero.calcular_mezcla(dinero.pesos(30), {20: 10, 50: 10}) is None
    assert dinero.calcular_mezcla(1050, {20: 10}) is None  # centavos sueltos


def test_formatear():
    assert dinero.formatear(123456) == "$1,234.56"
    assert dinero.formatear(-500) == "-$5.00"


def _existe_por_fuerza_bruta(monto_pesos, disp, max_b):
    denoms = [d for d in DENOMINACIONES_MXN if disp.get(d, 0) > 0]
    rangos = [range(min(disp[d], monto_pesos // d) + 1) for d in denoms]
    for combo in itertools.product(*rangos):
        if sum(c * d for c, d in zip(combo, denoms, strict=True)) == monto_pesos and sum(combo) <= max_b:
            return True
    return False


@settings(max_examples=150, deadline=None)
@given(
    monto=st.integers(min_value=1, max_value=120).map(lambda x: x * 10),
    disp=st.fixed_dictionaries({d: st.integers(0, 6) for d in DENOMINACIONES_MXN}),
    max_b=st.integers(1, 12),
)
def test_mezcla_es_correcta_y_completa(monto, disp, max_b):
    m = dinero.calcular_mezcla(dinero.pesos(monto), disp, max_billetes=max_b)
    if m is None:
        assert not _existe_por_fuerza_bruta(monto, disp, max_b)
    else:
        assert dinero.valor(m) == dinero.pesos(monto)
        assert all(n <= disp[d] for d, n in m.items())
        assert dinero.billetes(m) <= max_b
