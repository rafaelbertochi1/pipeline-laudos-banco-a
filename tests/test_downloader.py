"""Lógica do robô de download (banco_a_downloader.py) que não depende do navegador.

Período e lotes, código do App Autenticador, cache de laudos sem laudo
publicado, fila de downloads pela API e a escolha do PDF certo entre os
laudos da API. O navegador e a rede nunca são abertos: o Playwright só é
importado, e a API é trocada por respostas falsas."""
import base64
import json
from datetime import date

import pymupdf
import pytest

import banco_a_downloader as dl


def pdf_com_texto(texto):
    """PDF de uma página gerado na hora (nada de laudo real)."""
    documento = pymupdf.open()
    documento.new_page().insert_text((72, 72), texto)
    dados = documento.tobytes()
    documento.close()
    return dados


# --- período e lotes --------------------------------------------------------

def test_periodo_dividido_entre_as_abas():
    """Setembro em 6 abas: ~5 dias cada, contíguos, sem buraco nem sobreposição."""
    assert dl.dividir_periodo("01/09/2026", "30/09/2026", 6) == [
        ("01/09/2026", "05/09/2026"), ("06/09/2026", "10/09/2026"), ("11/09/2026", "15/09/2026"),
        ("16/09/2026", "20/09/2026"), ("21/09/2026", "25/09/2026"), ("26/09/2026", "30/09/2026"),
    ]


def test_periodo_com_menos_dias_que_abas():
    """Nunca abre aba pra sub-período vazio: no máximo uma por dia."""
    assert dl.dividir_periodo("01/09/2026", "03/09/2026", 6) == [
        ("01/09/2026", "01/09/2026"), ("02/09/2026", "02/09/2026"), ("03/09/2026", "03/09/2026"),
    ]
    assert dl.dividir_periodo("01/09/2026", "01/09/2026", 6) == [("01/09/2026", "01/09/2026")]


def test_periodo_com_divisao_nao_exata():
    assert dl.dividir_periodo("01/09/2026", "10/09/2026", 3) == [
        ("01/09/2026", "03/09/2026"), ("04/09/2026", "07/09/2026"), ("08/09/2026", "10/09/2026"),
    ]


def test_lotes_de_no_maximo_31_dias():
    """Período longo vai em lotes de até um mês (filtro largo perdia laudos)."""
    assert dl.dividir_em_lotes("01/01/2026", "15/03/2026", 31) == [
        ("01/01/2026", "31/01/2026"), ("01/02/2026", "03/03/2026"), ("04/03/2026", "15/03/2026"),
    ]


def test_lote_zero_e_o_periodo_inteiro():
    assert dl.dividir_em_lotes("01/01/2026", "15/03/2026", 0) == [("01/01/2026", "15/03/2026")]


@pytest.mark.parametrize("inicio, fim, esperado", [
    ("01/09/2026", "30/09/2026", ("01/09/2026", "30/09/2026")),
    ("1/9/2026", "30/9/2026", ("01/09/2026", "30/09/2026")),   # normaliza com zero à esquerda
    ("31/02/2026", "30/09/2026", None),                        # data inválida
    ("30/09/2026", "01/09/2026", None),                        # final antes da inicial
    ("01/09/2026", "01/01/2099", None),                        # final no futuro
])
def test_ler_periodo_das_variaveis(monkeypatch, inicio, fim, esperado):
    monkeypatch.setenv("PERIODO_INICIO", inicio)
    monkeypatch.setenv("PERIODO_FIM", fim)
    assert dl.ler_periodo() == esperado


@pytest.mark.parametrize("valor, esperado", [
    (None, 6),      # ausente: padrão
    ("8", 8),
    ("50", 12),     # acima do máximo
    ("0", 1),       # abaixo do mínimo
    ("seis", 6),    # inválido: padrão
])
def test_inteiro_env(monkeypatch, valor, esperado):
    if valor is None:
        monkeypatch.delenv("PARALELISMO_TESTE", raising=False)
    else:
        monkeypatch.setenv("PARALELISMO_TESTE", valor)
    assert dl._inteiro_env("PARALELISMO_TESTE", 6, 1, 12) == esperado


@pytest.mark.parametrize("segundos, esperado", [
    (59, "0min59s"), (61, "1min01s"), (3725, "1h02min05s"),
])
def test_formatar_duracao(segundos, esperado):
    assert dl.formatar_duracao(segundos) == esperado


def test_numero_da_proposta_na_linha_da_grade():
    assert dl.extrair_numero_proposta("INSP9324\n900000001\nCidade Exemplo") == "900000001"
    assert dl.extrair_numero_proposta("INSP9324 sem proposta 123") is None


# --- App Autenticador (TOTP) -----------------------------------------------

CHAVE_RFC = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize("instante, codigo", [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1234567890, "89005924"),
    (20000000000, "65353130"),
])
def test_totp_bate_com_os_vetores_da_rfc_6238(instante, codigo):
    """Mesmo cálculo do Google/Microsoft Authenticator (vetores SHA-1 da RFC)."""
    assert dl.gerar_codigo_totp(CHAVE_RFC, instante=instante, digitos=8) == codigo


def test_totp_padrao_tem_6_digitos_com_zero_a_esquerda():
    assert dl.gerar_codigo_totp(CHAVE_RFC, instante=1111111109) == "081804"


@pytest.mark.parametrize("entrada", [
    "jbsw y3dp ehpk 3pxp",
    "JBSW-Y3DP-EHPK-3PXP",
    "otpauth://totp/Plataforma:usuario.teste?secret=JBSWY3DPEHPK3PXP&issuer=Plataforma",
])
def test_normalizar_chave_totp(entrada):
    """A chave pode vir com espaço, hífen, minúscula ou como a URL do QR."""
    assert dl.normalizar_chave_totp(entrada) == "JBSWY3DPEHPK3PXP"


def test_chave_autenticador_invalida_e_ignorada(monkeypatch):
    monkeypatch.setenv("TESTE_CHAVE_AUTENTICADOR", "chave!com#simbolos")
    assert dl.chave_autenticador_configurada("TESTE") is None
    monkeypatch.setenv("TESTE_CHAVE_AUTENTICADOR", "jbsw y3dp ehpk 3pxp")
    assert dl.chave_autenticador_configurada("TESTE") == "JBSWY3DPEHPK3PXP"
    monkeypatch.delenv("TESTE_CHAVE_AUTENTICADOR")
    assert dl.chave_autenticador_configurada("TESTE") is None


def test_credenciais_precisam_de_usuario_e_senha(monkeypatch):
    monkeypatch.setenv("TESTE_USUARIO", " usuario.teste ")
    monkeypatch.delenv("TESTE_SENHA", raising=False)
    assert dl.credenciais_configuradas("TESTE") == (None, None)
    monkeypatch.setenv("TESTE_SENHA", "senha-de-teste")
    assert dl.credenciais_configuradas("TESTE") == ("usuario.teste", "senha-de-teste")


# --- cache de "sem laudo publicado" -----------------------------------------

def test_checado_recentemente(monkeypatch):
    """Laudo sem laudo publicado só é reaberto depois de N dias."""
    monkeypatch.setattr(dl, "RECHECAR_SEM_LAUDO_DIAS", 7)
    cache = {"INSP1": "2026-10-10", "INSP2": "2026-10-01", "INSP3": "data-quebrada"}
    hoje = date(2026, 10, 16)
    assert dl.checado_recentemente(cache, "INSP1", hoje) is True     # 6 dias
    assert dl.checado_recentemente(cache, "INSP2", hoje) is False    # 15 dias
    assert dl.checado_recentemente(cache, "INSP3", hoje) is False
    assert dl.checado_recentemente(cache, "INSP9", hoje) is False


def test_rechecar_zero_desliga_o_cache(monkeypatch):
    monkeypatch.setattr(dl, "RECHECAR_SEM_LAUDO_DIAS", 0)
    assert dl.checado_recentemente({"INSP1": "2026-10-16"}, "INSP1", date(2026, 10, 16)) is False


def test_cache_salvo_e_relido(tmp_path, monkeypatch):
    caminho = tmp_path / "laudos" / "_sem_laudo_publicado.json"
    monkeypatch.setattr(dl, "CACHE_SEM_LAUDO", str(caminho))
    assert dl.carregar_cache_sem_laudo() == {}                       # arquivo ainda não existe
    dl.salvar_cache_sem_laudo({"INSP2": "2026-10-02", "INSP1": "2026-10-01"})
    assert dl.carregar_cache_sem_laudo() == {"INSP1": "2026-10-01", "INSP2": "2026-10-02"}
    assert not (tmp_path / "laudos" / "_sem_laudo_publicado.json.tmp").exists()


def test_cache_corrompido_vira_vazio(tmp_path, monkeypatch):
    """Só perde a economia: todo laudo é checado de novo."""
    caminho = tmp_path / "_sem_laudo_publicado.json"
    caminho.write_text("{ isso não é json", encoding="utf-8")
    monkeypatch.setattr(dl, "CACHE_SEM_LAUDO", str(caminho))
    assert dl.carregar_cache_sem_laudo() == {}


def test_cache_ignora_entradas_de_tipo_errado(tmp_path, monkeypatch):
    caminho = tmp_path / "_sem_laudo_publicado.json"
    caminho.write_text(json.dumps({"INSP1": "2026-10-01", "INSP2": 20261001}), encoding="utf-8")
    monkeypatch.setattr(dl, "CACHE_SEM_LAUDO", str(caminho))
    assert dl.carregar_cache_sem_laudo() == {"INSP1": "2026-10-01"}


# --- laudo de inspeção e cabeçalhos da API -----------------------------------

def test_reconhece_laudo_de_inspecao():
    """O "LAUDO DE INSPEÇÃO" (só a vistoria) não pode ser salvo com o nome
    final: a avaliação de verdade nunca seria baixada depois."""
    assert dl.eh_laudo_de_inspecao(pdf_com_texto("LAUDO DE INSPECAO - vistoria")) is True
    assert dl.eh_laudo_de_inspecao(pdf_com_texto("LAUDO DE AVALIACAO #INSP0001")) is False


def test_pdf_ilegivel_nao_e_tratado_como_inspecao():
    assert dl.eh_laudo_de_inspecao(b"isto nao e um pdf") is False


def test_carimbo_de_hora_e_renovado(monkeypatch):
    """Cabeçalho com a hora da chamada original é trocado pela hora de agora."""
    monkeypatch.setattr(dl.time, "time", lambda: 1_790_000_000.5)
    assert dl._atualizar_carimbo("1789999000000") == "1790000000500"   # milissegundos
    assert dl._atualizar_carimbo("1789999000") == "1790000000"         # segundos
    assert dl._atualizar_carimbo("1000000000") == "1000000000"         # longe de agora: mantém
    assert dl._atualizar_carimbo("abc123") == "abc123"


def test_textos_de_um_json():
    assert dl._textos({"codigo": "INSP 9324", "itens": ["x-1", {"vazio": " "}], "n": 5}) == {
        "INSP 9324", "INSP", "9324", "x-1", "x", "1",
    }


# --- mapa código da grade -> id do item na API --------------------------------

class RespostaFalsa:
    def __init__(self, url, dados):
        self.url = url
        self._dados = dados

    def json(self):
        return self._dados


class PaginaFalsa:
    def __init__(self):
        self.ouvintes = {}

    def on(self, evento, funcao):
        self.ouvintes[evento] = funcao


def mapa_com(*conteudos):
    pagina = PaginaFalsa()
    mapa = dl.MapaItensGrade(pagina)
    for conteudo in conteudos:
        pagina.ouvintes["response"](RespostaFalsa(
            "https://api.exemplo.test/inspecao-360-api/v1/relatorios/inspecoes/analitico?page=0",
            {"content": conteudo}))
    pagina.ouvintes["response"](RespostaFalsa("https://api.exemplo.test/outra", {"content": []}))
    return mapa


def test_codigo_achado_no_item():
    mapa = mapa_com([
        {"protocolo": "P1", "itens": [{"id": 11, "codigo": "INSP0001"}, {"id": 12, "codigo": "INSP0002"}]},
    ])
    assert mapa.item_de("INSP0002") == 12


def test_codigo_da_inspecao_so_vale_com_item_unico():
    """Inspeção com um item só: o código pode estar na inspeção. Com vários
    itens, não dá pra saber qual - vai pela tela."""
    mapa = mapa_com([
        {"codigo": "INSP0003", "itens": [{"id": 31}]},
        {"codigo": "INSP0004", "itens": [{"id": 41}, {"id": 42}]},
    ])
    assert mapa.item_de("INSP0003") == 31
    assert mapa.item_de("INSP0004") is None


def test_codigo_ambiguo_ou_ausente_vai_pela_tela():
    mapa = mapa_com([
        {"itens": [{"id": 51, "codigo": "INSP0005"}]},
        {"itens": [{"id": 52, "codigo": "INSP0005"}]},
    ])
    assert mapa.item_de("INSP0005") is None
    assert mapa.item_de("INSP9999") is None


# --- fila única de downloads pela API ----------------------------------------

def pendente(laudo_id, item_id=7, grupo=("01/09/2026", "05/09/2026")):
    return (grupo, "900000001", laudo_id, f"/tmp/{laudo_id}.pdf", item_id)


def test_laudo_sem_item_na_api_vai_direto_pela_tela(monkeypatch):
    monkeypatch.setattr(dl, "DOWNLOAD_API", True)
    fila = dl.FilaApi([pendente("INSP1"), pendente("INSP2", item_id=None)], num_abas=1)
    assert fila.ativa is True
    assert fila.proximo()[2] == "INSP1"
    assert fila.proximo() is None
    assert list(fila.pela_tela[("01/09/2026", "05/09/2026")]) == ["INSP2"]


def test_api_desligada_manda_tudo_pela_tela(monkeypatch):
    monkeypatch.setattr(dl, "DOWNLOAD_API", False)
    fila = dl.FilaApi([pendente("INSP1")], num_abas=1)
    assert fila.ativa is False
    assert fila.proximo() is None
    assert "INSP1" in fila.pela_tela[("01/09/2026", "05/09/2026")]


def test_falhas_seguidas_desligam_a_api(monkeypatch):
    """Depois de N falhas seguidas a API é desligada uma vez só (o aviso sai
    uma vez); falha que não conta (caso da tela) não soma."""
    monkeypatch.setattr(dl, "DOWNLOAD_API", True)
    monkeypatch.setattr(dl, "DOWNLOADS_SIMULTANEOS", 3)
    itens = [pendente(f"INSP{i}") for i in range(6)]
    fila = dl.FilaApi(itens, num_abas=1)
    assert fila.limite_falhas == 3
    assert fila.falhou(itens[0], conta_como_falha=False) is False
    assert fila.falhou(itens[1], True) is False
    assert fila.falhou(itens[2], True) is False
    assert fila.falhou(itens[3], True) is True     # a terceira desliga
    assert fila.falhou(itens[4], True) is False    # já desligada: não avisa de novo
    assert fila.ativa is False
    assert len(fila.pela_tela[("01/09/2026", "05/09/2026")]) == 5


def test_sucesso_zera_as_falhas_seguidas(monkeypatch):
    monkeypatch.setattr(dl, "DOWNLOAD_API", True)
    monkeypatch.setattr(dl, "DOWNLOADS_SIMULTANEOS", 3)
    itens = [pendente(f"INSP{i}") for i in range(4)]
    fila = dl.FilaApi(itens, num_abas=1)
    fila.falhou(itens[0], True)
    fila.falhou(itens[1], True)
    fila.deu_certo("INSP2", "baixado", {"lista": 0.2, "pdf": 1.0})
    fila.deu_certo("INSP3", "sem_laudo", {"lista": 0.1})
    assert fila.falhas_seguidas == 0
    assert (fila.baixados, fila.sem_laudo, fila.ids_sem_laudo) == (1, 1, ["INSP3"])
    assert fila.tempos_lista == [0.2, 0.1] and fila.tempos_pdf == [1.0]


def test_ultima_aba_manda_o_que_sobrou_pra_tela(monkeypatch):
    monkeypatch.setattr(dl, "DOWNLOAD_API", True)
    fila = dl.FilaApi([pendente("INSP1"), pendente("INSP2")], num_abas=1)
    fila.proximo()                      # um saiu da fila, o outro sobrou
    fila.terminar_fase_api()
    assert fila.proximo() is None
    assert list(fila.pela_tela[("01/09/2026", "05/09/2026")]) == ["INSP2"]


# --- escolha do PDF entre os laudos da API ------------------------------------

class SessaoFalsa(dl.SessaoHttp):
    """SessaoHttp com as respostas da API trocadas por um dicionário."""

    def __init__(self, respostas):
        super().__init__("https://api.exemplo.test/v1/", {}, "")
        self.respostas = respostas

    def _get(self, caminho):
        return self.respostas[caminho]


def test_baixa_o_primeiro_laudo_de_avaliacao(tmp_path):
    avaliacao = pdf_com_texto("LAUDO DE AVALIACAO")
    sessao = SessaoFalsa({
        "laudos?itemInspecao=7": json.dumps([{"id": 1}, {"id": 2}]).encode(),
        "laudos/1/pdf?tipoLaudo=1": pdf_com_texto("LAUDO DE INSPECAO"),
        "laudos/2/pdf?tipoLaudo=1": avaliacao,
    })
    destino = tmp_path / "900000001_INSP1.pdf"
    tempos = {}
    assert sessao.baixar(7, str(destino), tempos) == "baixado"
    assert destino.read_bytes() == avaliacao
    assert tempos["inspecao_pulada"] == 1
    assert not (tmp_path / "900000001_INSP1.pdf.tmp").exists()


def test_so_laudo_de_inspecao_conta_como_sem_laudo(tmp_path):
    sessao = SessaoFalsa({
        "laudos?itemInspecao=7": json.dumps({"content": [{"id": 1}]}).encode(),
        "laudos/1/pdf?tipoLaudo=1": pdf_com_texto("LAUDO DE INSPECAO"),
    })
    destino = tmp_path / "a.pdf"
    tempos = {}
    assert sessao.baixar(7, str(destino), tempos) == "sem_laudo"
    assert tempos["so_inspecao"] == 1
    assert not destino.exists()


def test_sem_laudo_publicado(tmp_path):
    sessao = SessaoFalsa({
        "laudos?itemInspecao=7": b"[]",
        "laudosFrustro?itemInspecao=7": b"[]",
    })
    assert sessao.baixar(7, str(tmp_path / "a.pdf"), {}) == "sem_laudo"


def test_laudo_frustro_fica_pra_tela(tmp_path):
    """Vistoria que não aconteceu: a tela decide melhor que a API."""
    sessao = SessaoFalsa({
        "laudos?itemInspecao=7": b"[]",
        "laudosFrustro?itemInspecao=7": b'[{"id": 9}]',
    })
    with pytest.raises(dl.SoPelaTela):
        sessao.baixar(7, str(tmp_path / "a.pdf"), {})


@pytest.mark.parametrize("respostas, mensagem", [
    ({"laudos?itemInspecao=7": b"<html>erro</html>"}, "não é JSON"),
    ({"laudos?itemInspecao=7": b'[{"nome": "sem id"}]'}, "sem id"),
    ({"laudos?itemInspecao=7": b'[{"id": 1}]', "laudos/1/pdf?tipoLaudo=1": b"<html>login</html>"}, "não é um PDF"),
])
def test_resposta_estranha_da_api_e_erro(tmp_path, respostas, mensagem):
    """Qualquer resposta fora do esperado vira ErroApi (o laudo cai pra tela)
    e nada é gravado no destino."""
    destino = tmp_path / "a.pdf"
    with pytest.raises(dl.ErroApi, match=mensagem):
        SessaoFalsa(respostas).baixar(7, str(destino), {})
    assert not destino.exists()
