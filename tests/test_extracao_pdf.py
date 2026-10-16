"""extrair_dados_pdf de ponta a ponta, com o pdfplumber trocado por um falso.

O texto abaixo imita um Laudo Físico do Banco A (dados fictícios). Assim
dá pra testar a montagem da linha que vai pro banco - valor unitário,
modelo, campos não lidos, laudo de inspeção - sem PDF de verdade."""
from decimal import Decimal

import pytest

import banco_a_extractor as extrator

LAUDO_FISICO = """LAUDO DE AVALIAÇÃO #INSP000123
Solicitante Nº da Proposta Tipo de Laudo
BANCO_A 900000001 Laudo Físico
Endereço Número Complemento
RUA DAS ACÁCIAS 100 APTO 12
Coordenadas do Imóvel
-23.5505, -46.6333
01 - Tipo do Imóvel 02 - Uso do Imóvel
Apartamento Residencial
03 - Idade Aparente 04 - Estado de Conservação do Imóvel
10 Bom
05 - Padrão de Acabamento do Imóvel 06 - Total de Dormitórios
Normal 2
07 - Nº de Suítes 08 - Total de Banheiros
1 2
09 - Total de Vagas 10 - Área Comum (em m²)
1 30,50
11 - Área Averbada (em m²) 12 - Área Total (em m²)
80,00 110,50
Data da Vistoria
15/08/2026
VALOR DA AVALIAÇÃO
R$ 400.000,00
"""
AREAS = "11 - Área Averbada (em m²) 12 - Área Total (em m²)\n80,00 110,50\n"


class PaginaFalsa:
    def __init__(self, texto):
        self.texto = texto

    def extract_text(self):
        return self.texto


class PdfFalso:
    def __init__(self, paginas):
        self.pages = [PaginaFalsa(t) for t in paginas]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def pdf_com(monkeypatch):
    """Faz o pdfplumber "abrir" um PDF com as páginas de texto dadas e
    desliga a extração de imagens (que abriria o arquivo de verdade)."""
    monkeypatch.setattr(extrator, "extrair_imagens_do_laudo", lambda caminho: True)

    def configurar(*paginas):
        monkeypatch.setattr(extrator.pdfplumber, "open", lambda caminho: PdfFalso(paginas))
    return configurar


def test_laudo_fisico_completo(pdf_com):
    pdf_com(LAUDO_FISICO)
    resultado = extrator.extrair_dados_pdf("data/laudos/900000001_INSP000123.pdf")
    assert resultado["status"] == "ok"
    assert resultado["nao_lidos"] == []
    assert resultado["imagens_verificadas"] is True
    d = resultado["dados"]
    assert d["path"] == "900000001_INSP000123.pdf"
    assert d["codigo_laudo"] == "INSP000123"
    assert d["numero_proposta"] == "900000001"
    assert d["modelo_usado"] == "fisico"
    assert (d["endereco"], d["numero"], d["complemento"]) == ("RUA DAS ACÁCIAS", "100", "APTO 12")
    assert (d["tipo_imovel"], d["padrao_acabamento"], d["estado_conservacao"]) == ("Apartamento", "Normal", "Bom")
    assert (d["quartos"], d["suites"], d["banheiros"], d["vagas"], d["idade_anos"]) == (2, 1, 2, 1, 10)
    assert d["area_privativa_m2"] == Decimal("80.00")
    assert d["area_comum_m2"] == Decimal("30.50")
    assert d["area_total_m2"] == Decimal("110.50")
    assert d["area_terreno_m2"] == Decimal("0.00")
    assert d["valor_mercado"] == Decimal("400000.00")
    assert d["valor_venda_forcada"] == Decimal("0.00")   # o Banco A não tem esse conceito
    assert d["valor_unitario_m2"] == Decimal("5000.00")  # 400.000 / 80 m²
    assert d["data_avaliacao"] == "15/08/2026"
    assert (d["latitude"], d["longitude"]) == (-23.5505, -46.6333)


def test_laudo_correto_passa_nas_checagens(pdf_com):
    """O laudo de exemplo não dispara nenhuma checagem do lote."""
    import checagens
    pdf_com(LAUDO_FISICO)
    resultado = extrator.extrair_dados_pdf("x.pdf")
    assert checagens.problemas_dos_dados(resultado["dados"], resultado["nao_lidos"]) == []


def test_texto_dividido_em_paginas(pdf_com):
    """As páginas são juntadas antes da leitura (rótulo e valor podem ficar
    em páginas diferentes do texto, mas os campos continuam achados)."""
    meio = LAUDO_FISICO.index("Data da Vistoria")
    pdf_com(LAUDO_FISICO[:meio], LAUDO_FISICO[meio:])
    d = extrator.extrair_dados_pdf("x.pdf")["dados"]
    assert d["data_avaliacao"] == "15/08/2026"
    assert d["valor_mercado"] == Decimal("400000.00")


def test_laudo_eletronico(pdf_com):
    pdf_com(LAUDO_FISICO.replace("Laudo Físico", "Laudo Eletrônico"))
    assert extrator.extrair_dados_pdf("x.pdf")["dados"]["modelo_usado"] == "eletronico"


def test_terreno_usa_a_area_do_terreno_no_valor_unitario(pdf_com):
    """Sem área privativa (terreno), o valor unitário sai pela área do terreno."""
    texto = (LAUDO_FISICO.replace(AREAS, "12 - Área Total (em m²)\n110,50\n")
             + "TERRENO\n20 - Área Total (em m²) 21 - Fração Ideal\n500,00 100%\n")
    pdf_com(texto)
    d = extrator.extrair_dados_pdf("x.pdf")["dados"]
    assert d["area_privativa_m2"] == Decimal("0.00")
    assert d["area_terreno_m2"] == Decimal("500.00")
    assert d["valor_unitario_m2"] == Decimal("800.00")   # 400.000 / 500 m²


def test_sem_area_valor_unitario_zero(pdf_com):
    """Sem área nenhuma, valor unitário zero em vez de divisão por zero."""
    pdf_com(LAUDO_FISICO.replace(AREAS, ""))
    d = extrator.extrair_dados_pdf("x.pdf")["dados"]
    assert d["area_privativa_m2"] == Decimal("0.00")
    assert d["valor_unitario_m2"] == Decimal("0.00")


def test_area_averbada_em_branco_pega_o_numero_vizinho(pdf_com):
    """Bug conhecido: _area_averbada procura o próximo número com vírgula
    até 200 caracteres depois do rótulo (pra pular os nomes de cômodo do
    modelo Eletrônico). Se a Área Averbada vier em branco, ela pega o
    número da coluna ao lado - aqui a Área Total (110,50) vira a área
    privativa. O teste documenta o comportamento atual; quando for
    corrigido, o esperado passa a ser zero."""
    pdf_com(LAUDO_FISICO.replace("80,00 110,50", "- 110,50"))
    d = extrator.extrair_dados_pdf("x.pdf")["dados"]
    assert d["area_privativa_m2"] == Decimal("110.50")


def test_campos_nao_achados_sao_gravados_com_padrao_e_avisados(pdf_com):
    """Sem os rótulos de tipo/padrão/estado, grava o valor padrão mas
    devolve a lista `nao_lidos` pra validação do lote contar."""
    texto = (LAUDO_FISICO
             .replace("Tipo do Imóvel", "Categoria")
             .replace("Padrão de Acabamento do Imóvel", "Acabamento")
             .replace("Estado de Conservação do Imóvel", "Conservação"))
    pdf_com(texto)
    resultado = extrator.extrair_dados_pdf("x.pdf")
    d = resultado["dados"]
    assert (d["tipo_imovel"], d["padrao_acabamento"], d["estado_conservacao"]) == ("Apartamento", "Normal", "Bom")
    assert resultado["nao_lidos"] == ["tipo_imovel", "padrao_acabamento", "estado_conservacao"]


def test_laudo_de_inspecao_nao_e_gravado(pdf_com):
    """"LAUDO DE INSPEÇÃO" é só a vistoria: gravar o path impediria a
    avaliação de verdade (mesmo nome de arquivo) de ser extraída depois."""
    pdf_com("LAUDO DE INSPEÇÃO #INSP000123\nDados da vistoria...")
    assert extrator.extrair_dados_pdf("data/laudos/a.pdf") == {"status": "inspecao", "path": "a.pdf"}


def test_pdf_sem_texto(pdf_com):
    """PDF escaneado como imagem não tem texto extraível."""
    pdf_com(None, "   ")
    assert extrator.extrair_dados_pdf("a.pdf") == {"status": "vazio", "path": "a.pdf"}


def test_erro_ao_abrir_o_pdf_vira_resultado(monkeypatch):
    """O worker nunca levanta exceção: devolve o erro pro processo principal."""
    monkeypatch.setattr(extrator, "extrair_imagens_do_laudo", lambda caminho: False)

    def quebrar(caminho):
        raise ValueError("arquivo corrompido")
    monkeypatch.setattr(extrator.pdfplumber, "open", quebrar)
    assert extrator.extrair_dados_pdf("a.pdf") == {"status": "erro", "path": "a.pdf",
                                                   "mensagem": "arquivo corrompido"}


def test_falha_nas_imagens_nao_derruba_a_extracao(pdf_com, monkeypatch):
    def quebrar(caminho):
        raise RuntimeError("imagem com defeito")
    pdf_com(LAUDO_FISICO)
    monkeypatch.setattr(extrator, "extrair_imagens_do_laudo", quebrar)
    resultado = extrator.extrair_dados_pdf("x.pdf")
    assert resultado["status"] == "ok"
    assert resultado["imagens_verificadas"] is False
