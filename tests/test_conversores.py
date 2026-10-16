"""Leitura dos campos do PDF e conversão pros tipos do banco (banco_a_extractor.py).

O laudo do Banco A é uma tabela de duas colunas: uma linha com os rótulos
("NN - Rótulo A   MM - Rótulo B") e, embaixo, a linha com os valores na
mesma ordem. Os textos abaixo imitam o que o pdfplumber devolve desses
PDFs; todos os dados são fictícios."""
from decimal import Decimal

import pytest

from banco_a_extractor import (
    _area_averbada,
    _campo_numerico,
    _campo_texto,
    _campo_texto_varias_palavras,
    _coordenada_de_texto,
    _linha_e_indice_coluna,
    converter_float_coordenada,
    converter_float_seguro,
    converter_int_seguro,
    extrair_area_terreno,
    extrair_codigo_laudo,
    extrair_coordenadas,
    extrair_data_vistoria,
    extrair_endereco_numero_complemento,
    extrair_numero_proposta_e_tipo_laudo,
    extrair_valor_avaliacao,
    limpar_txt,
)

TABELA = (
    "01 - Tipo do Imóvel 02 - Uso do Imóvel\n"
    "Sala Comercial Comercial\n"
    "03 - Idade Aparente 04 - Estado de Conservação do Imóvel\n"
    "12 Em Construção\n"
    "05 - Padrão de Acabamento do Imóvel 06 - Total de Dormitórios\n"
    "Normal 3\n"
)


# --- conversões -------------------------------------------------------------

@pytest.mark.parametrize("entrada, esperado", [
    ("1.234,56", "1234.56"),          # formato brasileiro
    ("R$ 450.000,00", "450000.00"),   # com símbolo de moeda
    ("1.234.567", "1234567.00"),      # só separador de milhar
    ("216.04", "216.04"),             # ponto decimal com 2 casas
    ("1.234", "1234.00"),             # ponto + 3 dígitos = milhar, não decimal
    (8666.299999999999, "8666.30"),   # float com sobra binária vira Decimal limpo
    (Decimal("95.305"), "95.30"),     # Decimal só é arredondado (meio-par)
    ("95,30", "95.30"),
    (None, "0.00"),
    ("—", "0.00"),
    ("NULL", "0.00"),
    ("sem número", "0.00"),
])
def test_converter_float_seguro(entrada, esperado):
    """Valores e áreas vão pra colunas NUMERIC: nunca float com sobra binária,
    e texto que não é número vira zero em vez de derrubar a carga."""
    assert converter_float_seguro(entrada) == Decimal(esperado)


@pytest.mark.parametrize("entrada, esperado", [
    ("3", 3),
    ("2 quartos", 2),
    ("999", 999),
    ("1000", 0),             # nenhum desses campos chega a mil
    ("123.456.789-09", 0),   # CPF pego por engano não estoura o INTEGER do banco
    ("-1", 0),
    ("abc", 0),
    (None, 0),
])
def test_converter_int_seguro(entrada, esperado):
    """Quartos, banheiros, vagas e idade só aceitam 0 a 999."""
    assert converter_int_seguro(entrada) == esperado


@pytest.mark.parametrize("entrada, esperado", [
    ("-23.5505", -23.5505),
    (" -46.6333 ", -46.6333),
    (12, 12.0),
    ("abc", None),
    (None, None),
])
def test_converter_float_coordenada(entrada, esperado):
    assert converter_float_coordenada(entrada) == esperado


@pytest.mark.parametrize("entrada, padrao, esperado", [
    ("  Centro ", "", "Centro"),
    ("NULL", "", ""),
    ("null", "Bom", "Bom"),
    (None, "S/N", "S/N"),
    ("", "Normal", "Normal"),
])
def test_limpar_txt(entrada, padrao, esperado):
    """Campo vazio ou "NULL" vira o valor padrão (é o que o extrator grava
    quando não acha tipo/padrão/estado)."""
    assert limpar_txt(entrada, padrao) == esperado


# --- leitura por coluna da tabela -------------------------------------------

def test_indice_da_coluna_conta_os_rotulos_irmaos():
    """O 2º rótulo da linha de cabeçalho pega o 2º token da linha de valores."""
    tokens, indice = _linha_e_indice_coluna(TABELA, r"Uso\s+do\s+Im[óo]vel")
    assert tokens == ["Sala", "Comercial", "Comercial"]
    assert indice == 1
    _, indice = _linha_e_indice_coluna(TABELA, r"Tipo\s+do\s+Im[óo]vel")
    assert indice == 0


def test_rotulo_ausente():
    assert _linha_e_indice_coluna("texto sem a tabela", r"Tipo\s+do\s+Im[óo]vel") == (None, None)
    assert _campo_texto("texto sem a tabela", r"Tipo\s+do\s+Im[óo]vel") is None
    assert _campo_numerico("texto sem a tabela", r"Idade\s+Aparente", tipo="int") is None


def test_campos_de_uma_palavra_e_numericos():
    assert _campo_texto(TABELA, r"Padr[ãa]o\s+de\s+Acabamento\s+do\s+Im[óo]vel") == "Normal"
    assert _campo_numerico(TABELA, r"Total\s+de\s+Dormit[óo]rios", tipo="int") == "3"
    assert _campo_numerico(TABELA, r"Idade\s+Aparente", tipo="int") == "12"


def test_tipo_e_estado_de_varias_palavras_nao_sao_cortados():
    """Bug corrigido em out/2026: gravava "Sala" em vez de "Sala Comercial"
    e "Em" em vez de "Em Construção" (checagem "tipo/estado com palavra
    cortada" impede que volte)."""
    assert _campo_texto_varias_palavras(TABELA, r"Tipo\s+do\s+Im[óo]vel") == "Sala Comercial"
    assert _campo_texto_varias_palavras(
        TABELA, r"Estado\s+de\s+Conserva[çc][ãa]o\s+do\s+Im[óo]vel") == "Em Construção"


def test_area_averbada_ao_lado_de_comodo_com_varias_palavras():
    """No modelo Eletrônico a área fica ao lado da lista de cômodos; contar
    tokens erraria a coluna quando o 1º cômodo é "Sala de Estar"."""
    texto = "08 - Divisão Interna 07 - Área Averbada (em m²)\nSala de Estar 85,40 Cozinha"
    assert _area_averbada(texto) == "85,40"
    assert _area_averbada("sem área") is None


def test_area_do_terreno_so_depois_da_secao_terreno():
    """A "Área Total" da construção vem antes; a do terreno só existe em
    imóvel isolado. Condomínio (só fração ideal) fica sem área de terreno."""
    isolado = ("05 - Área Total (em m²)\n90,00\n"
               "TERRENO\n10 - Área Total (em m²) 11 - Fração Ideal\n250,00 100%\n")
    assert extrair_area_terreno(isolado) == "250,00"
    assert extrair_area_terreno("05 - Área Total (em m²)\n90,00\nTERRENO\n10 - Fração Ideal (%)\n2,5") is None
    assert extrair_area_terreno("laudo sem seção de terreno") is None


# --- campos soltos ----------------------------------------------------------

def test_codigo_do_laudo():
    assert extrair_codigo_laudo("RELATÓRIO #INSP000123 - Plataforma") == "INSP000123"
    assert extrair_codigo_laudo("sem código") is None


def test_numero_da_proposta_e_tipo_do_laudo():
    texto = "Solicitante Nº da Proposta Tipo de Laudo\nBANCO_A 900000001 Laudo Eletrônico\n"
    assert extrair_numero_proposta_e_tipo_laudo(texto) == ("900000001", "Laudo Eletrônico")
    assert extrair_numero_proposta_e_tipo_laudo("nada aqui") == ("", "")


@pytest.mark.parametrize("linha, esperado", [
    ("RUA DAS ACÁCIAS 100 APTO 12", ("RUA DAS ACÁCIAS", "100", "APTO 12")),
    ("AVENIDA EXEMPLO S/N", ("AVENIDA EXEMPLO", "S/N", "")),
    # complemento vazio: o pdfplumber gruda o cabeçalho da linha seguinte
    ("RUA FICTÍCIA 45 CEP Bairro Município UF", ("RUA FICTÍCIA", "45", "")),
])
def test_endereco_numero_complemento(linha, esperado):
    texto = f"Endereço Número Complemento\n{linha}\n"
    assert extrair_endereco_numero_complemento(texto) == esperado


def test_endereco_ausente():
    assert extrair_endereco_numero_complemento("sem endereço") == ("", "S/N", "")


def test_data_da_vistoria():
    assert extrair_data_vistoria("Data da Vistoria Hora\n15/08/2026 10:00\n") == "15/08/2026"


@pytest.mark.parametrize("assinatura, esperado", [
    ("Cidade Exemplo, terça-feira, 1 de Setembro de 2026", "01/09/2026"),
    ("Cidade Exemplo, sexta-feira, 13 de março de 2026", "13/03/2026"),
])
def test_laudo_eletronico_usa_a_data_da_assinatura(assinatura, esperado):
    """O Laudo Eletrônico não tem seção de vistoria: a data por extenso da
    assinatura entra no lugar."""
    assert extrair_data_vistoria(assinatura) == esperado


def test_sem_data():
    assert extrair_data_vistoria("segunda-feira, 2 de Brumário de 2026") is None
    assert extrair_data_vistoria("sem data nenhuma") is None


def test_valor_da_avaliacao():
    assert extrair_valor_avaliacao("VALOR DA AVALIAÇÃO\nR$ 450.000,00\n") == "450.000,00"
    assert extrair_valor_avaliacao("sem valor") is None


# --- coordenadas digitadas à mão pelo vistoriador ---------------------------

@pytest.mark.parametrize("linha, esperado", [
    ("-23.49, -46.88", (-23.49, -46.88)),
    ("-23.49,-46.88", (-23.49, -46.88)),
    ("(-23.49, -46.88)", (-23.49, -46.88)),
    ("-23.49 -46.88", (-23.49, -46.88)),
    ("-23.49;-46.88", (-23.49, -46.88)),
    ("-11,42, -61,46", (-11.42, -61.46)),          # vírgula decimal
    ("22,9° S, 43,1° O", (-22.9, -43.1)),          # decimal com hemisfério
    ("norte", (None, None)),                       # texto solto
    ("-23.49", (None, None)),                      # só um número
])
def test_formatos_de_coordenada(linha, esperado):
    assert _coordenada_de_texto(linha) == esperado


def test_graus_minutos_segundos():
    lat, lon = _coordenada_de_texto("20°28'58.72\"S 47°25'46.23\"W")
    assert lat == pytest.approx(-20.4829778)
    assert lon == pytest.approx(-47.4295083)


def test_graus_minutos_segundos_por_extenso():
    """O "e" minúsculo que liga as duas partes não pode virar hemisfério."""
    lat, lon = _coordenada_de_texto("23° 30' 0\" Sul e 46° 37' 30\" Oeste")
    assert lat == pytest.approx(-23.5)
    assert lon == pytest.approx(-46.625)


def test_coordenadas_do_laudo():
    texto = "Coordenadas do Imóvel (lat, long)\n-23.5505, -46.6333\n"
    assert extrair_coordenadas(texto) == ("-23.5505, -46.6333", -23.5505, -46.6333)


@pytest.mark.parametrize("linha", ["22.99, -43.44", "22.99, 43.44"])
def test_sinal_esquecido_e_corrigido(linha):
    """O Brasil não tem longitude positiva nem latitude acima de 6: o "-"
    esquecido é recolocado."""
    _, lat, lon = extrair_coordenadas(f"Coordenadas do Imóvel\n{linha}\n")
    assert (lat, lon) == (-22.99, -43.44)


@pytest.mark.parametrize("linha", ["410093S 382802w", "10.5, -10.5", "norte"])
def test_coordenada_fora_do_brasil_fica_vazia(linha):
    """Melhor sem coordenada do que uma errada."""
    assert extrair_coordenadas(f"Coordenadas do Imóvel\n{linha}\n") == (None, None, None)
