"""Validação do lote antes de gravar (checagens.py e validar_lote).

Se uma checagem passa do limite, o lote inteiro não é gravado: é o que
impede um layout novo de PDF de encher o banco de dados errados."""
from decimal import Decimal

import pytest

import checagens
from banco_a_extractor import validar_lote


def laudo(**mudancas):
    base = {
        "path": "laudo_1.pdf",
        "codigo_laudo": "INSP0001",
        "numero_proposta": "900000001",
        "data_avaliacao": "15/08/2026",
        "tipo_imovel": "Apartamento",
        "valor_mercado": Decimal("400000"),
        "area_privativa_m2": Decimal("80"),
        "area_terreno_m2": Decimal("0"),
        "endereco": "RUA DAS ACÁCIAS",
        "quartos": 2,
        "banheiros": 1,
        "padrao_acabamento": "Normal",
        "estado_conservacao": "Bom",
        "latitude": -23.55,
        "longitude": -46.63,
    }
    base.update(mudancas)
    return base


def nomes(problemas):
    return {checagem for checagem, _ in problemas}


def lote(n=20, **mudancas):
    return [laudo(path=f"l{i}.pdf", codigo_laudo=f"INSP{i:04d}", **mudancas) for i in range(n)]


@pytest.mark.parametrize("valor, esperado", [
    ("Normal", False),
    ("Em Construção", False),
    ("4 Regular 60", True),     # número = pedaço de outra coluna
    ("x" * 41, True),           # comprido demais
    (None, False),
])
def test_texto_contaminado(valor, esperado):
    assert checagens.texto_contaminado(valor) is esperado


def test_laudo_correto_nao_tem_problemas():
    assert checagens.problemas_dos_dados(laudo()) == []


@pytest.mark.parametrize("mudanca, checagem", [
    ({"codigo_laudo": None}, "sem código de laudo"),
    ({"numero_proposta": ""}, "sem número de proposta"),
    ({"tipo_imovel": ""}, "sem tipo de imóvel"),
    ({"valor_mercado": Decimal("0")}, "valor de mercado zerado"),
    ({"area_privativa_m2": 0, "area_terreno_m2": 0}, "sem área (privativa e terreno zeradas)"),
    ({"endereco": ""}, "sem endereço"),
    ({"data_avaliacao": None}, "sem data de avaliação"),
    ({"padrao_acabamento": "4 Regular 60"}, "padrão/estado com pedaço de outra coluna"),
    ({"quartos": 16}, "quartos/banheiros acima de 15"),
    ({"tipo_imovel": "Sala"}, "tipo/estado com palavra cortada"),
    ({"estado_conservacao": "Em"}, "tipo/estado com palavra cortada"),
    ({"longitude": None}, "sem coordenadas"),
])
def test_cada_problema_e_detectado(mudanca, checagem):
    assert checagem in nomes(checagens.problemas_dos_dados(laudo(**mudanca)))


def test_terreno_sem_area_privativa_nao_e_problema():
    """Lote/terreno não tem área privativa; basta ter a do terreno."""
    d = laudo(tipo_imovel="Terreno", area_privativa_m2=0, area_terreno_m2=Decimal("250"))
    assert checagens.problemas_dos_dados(d) == []


def test_campo_gravado_com_valor_padrao_e_contado():
    """O parser grava "Apartamento"/"Normal"/"Bom" quando não acha o campo,
    então só a lista `nao_lidos` mostra a falha."""
    problemas = checagens.problemas_dos_dados(laudo(), nao_lidos=["tipo_imovel", "padrao_acabamento"])
    assert problemas == [("tipo/padrão/estado não lido (gravado com o valor padrão)",
                          "tipo_imovel, padrao_acabamento")]


@pytest.mark.parametrize("contagem, estourou", [
    ({"sem endereço": 9}, True),             # limite 5% de 100 + folga de 3 = 8
    ({"sem endereço": 8}, False),
    ({"sem coordenadas": 7}, True),          # limite 3% de 100 + folga de 3 = 6
    ({"sem coordenadas": 6}, False),
    ({"sem código de laudo": 4}, True),      # bug conhecido: limite 0 + folga 3
    ({"sem código de laudo": 3}, False),
    ({"checagem nova": 4}, True),            # checagem sem limite cadastrado conta como 0
])
def test_checagens_estouradas(contagem, estourou):
    assert bool(checagens.checagens_estouradas(contagem, 100)) is estourou


def test_lote_bom_e_gravado():
    assert validar_lote(lote()) is True


def test_layout_novo_bloqueia_o_lote(monkeypatch):
    monkeypatch.delenv("VALIDACAO_IGNORAR", raising=False)
    assert validar_lote(lote(endereco="")) is False


def test_tipo_nao_lido_em_todo_o_lote_bloqueia(monkeypatch):
    """Se o rótulo "Tipo do Imóvel" mudar, todo laudo viraria "Apartamento"
    sem ninguém perceber - a contagem de `nao_lidos` barra o lote."""
    monkeypatch.delenv("VALIDACAO_IGNORAR", raising=False)
    dados = lote()
    nao_lidos = {d["path"]: ["tipo_imovel"] for d in dados}
    assert validar_lote(dados, nao_lidos_por_path=nao_lidos) is False


def test_erros_de_pdf_entram_na_conta(monkeypatch):
    """PDF que deu erro conta no total e na checagem "erro ao processar o PDF"."""
    monkeypatch.delenv("VALIDACAO_IGNORAR", raising=False)
    erros = [(f"quebrado_{i}.pdf", "arquivo corrompido") for i in range(4)]
    assert validar_lote(lote(), erros) is False
    assert validar_lote(lote(), erros[:3]) is True


def test_variavel_de_ambiente_permite_gravar_mesmo_assim(monkeypatch):
    monkeypatch.setenv("VALIDACAO_IGNORAR", "1")
    assert validar_lote(lote(endereco="")) is True
