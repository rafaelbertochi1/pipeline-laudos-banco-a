"""Classificação e nome dos arquivos das fotos do laudo (banco_a_imagens.py).

As legendas são as digitadas pelo vistoriador embaixo de cada foto do
relatório fotográfico; os exemplos imitam os formatos que aparecem."""
import json
import multiprocessing

import pymupdf
import pytest

import banco_a_imagens as im


@pytest.mark.parametrize("legenda, label", [
    ("Fachada", "fachada"),
    ("Sala de estar", "sala"),
    ("Foto 3 - Banheiro social", "banheiro"),      # numeração na frente é ignorada
    ("Imagem 2: cozinha americana", "cozinha"),
    ("Área de serviço", "area_servico"),           # acento não importa
    ("BH social", "banheiro"),                     # abreviação real
    ("Padrão de luz", "medidor"),
    ("Vista do quarto", "quarto"),                 # 2ª passada: palavra em qualquer posição
    ("Vista da rua", "entorno"),
])
def test_classificar_legenda(legenda, label):
    assert im.classificar_legenda(legenda) == label


@pytest.mark.parametrize("legenda, label", [
    ("Salão de festas", "salao_festas"),
    ("Sala de jogos", "area_comum"),
    ("Sala de ginástica", "academia"),
])
def test_area_comum_nao_vira_sala_do_imovel(legenda, label):
    """No Banco B, "Salão de festas" e "Sala de jogos" viravam a foto da
    sala do imóvel; a ordem da lista desempata a favor da área comum."""
    assert im.classificar_legenda(legenda) == label


@pytest.mark.parametrize("legenda, label", [
    ("", "sem_legenda"),
    ("   ", "sem_legenda"),
    (None, "sem_legenda"),
    ("Foto 1 -", "outro"),            # só numeração
    ("Xpto qualquer", "outro"),
])
def test_legenda_vazia_ou_desconhecida(legenda, label):
    assert im.classificar_legenda(legenda) == label


def test_slug_da_legenda():
    assert im._slug("Árvore no Quintal!! dos fundos da casa") == "arvore-no-quintal-dos-fundos-d"
    assert im._slug("!!!") == "sem-texto"


@pytest.mark.parametrize("label, n, legenda, sufixo", [
    ("sala", 1, "Sala", "sala"),                 # 1ª foto mantém o nome antigo
    ("sala", 2, "Sala", "sala_2"),
    ("fachada", 1, "Fachada", "fachada_2"),      # a 1ª fachada é o _img
    ("outro", 3, "Árvore grande", "outro_3_arvore-grande"),
])
def test_sufixo_do_arquivo_e_o_caminho_de_volta(label, n, legenda, sufixo):
    """O nome do arquivo tem que levar de volta ao label (é assim que a
    etapa sabe o que já foi salvo na pasta)."""
    assert im._sufixo_arquivo(label, n, legenda) == sufixo
    assert im._label_do_sufixo(sufixo) == label


def test_img_e_a_fachada():
    assert im._label_do_sufixo("img") == "fachada"


def test_arquivos_do_laudo():
    arquivos = {"900_INSP1_img": "900_INSP1_img.jpeg", "900_INSP1_sala": "900_INSP1_sala.png",
                "900_INSP10_img": "900_INSP10_img.jpeg"}
    assert im._arquivos_do_laudo("pasta", "900_INSP1", arquivos) == {
        "img": "900_INSP1_img.jpeg", "sala": "900_INSP1_sala.png",
    }


# --- legenda embaixo da foto -------------------------------------------------

def linhas(*itens):
    """{(bloco, linha): [(x0, y0, x1, y1, palavra)]} como _linhas_de_texto devolve."""
    saida = {}
    for i, (x0, y0, texto) in enumerate(itens):
        palavras = texto.split()
        saida[(i, 0)] = [(x0 + 40 * j, y0, x0 + 40 * j + 35, y0 + 10, p) for j, p in enumerate(palavras)]
    return saida


FOTO = pymupdf.Rect(100, 100, 300, 250)


def test_legenda_mais_proxima_abaixo_da_foto():
    texto = linhas((110, 270, "Sala de estar"), (110, 320, "Cozinha"))
    assert im._legenda_da_foto(FOTO, texto) == "Sala de estar"


def test_foto_deitada_ainda_acha_a_legenda():
    """Foto deitada fica mais longe da legenda (até 100 pontos)."""
    assert im._legenda_da_foto(FOTO, linhas((110, 340, "Quarto"))) == "Quarto"
    assert im._legenda_da_foto(FOTO, linhas((110, 360, "Quarto"))) is None


def test_texto_de_outra_coluna_nao_e_legenda():
    assert im._legenda_da_foto(FOTO, linhas((400, 270, "Banheiro"))) is None


@pytest.mark.parametrize("rodape", ["Página 3 de 10", "3/10", "powered by", "RELATÓRIO FOTOGRÁFICO"])
def test_rodape_e_titulo_nao_sao_legenda(rodape):
    assert im._legenda_da_foto(FOTO, linhas((110, 270, rodape))) is None


# --- lista de PDFs já verificados --------------------------------------------

@pytest.fixture
def pasta_imagens(tmp_path, monkeypatch):
    monkeypatch.setattr(im, "PASTA_IMAGENS", str(tmp_path))
    monkeypatch.setattr(im, "CACHE_VERIFICADOS", str(tmp_path / "_pdfs_verificados.json"))
    monkeypatch.setattr(im, "CACHE_RESERVA", str(tmp_path / "_pdfs_verificados.json.reserva"))
    monkeypatch.setattr(im, "_verificados_do_processo", None)
    return tmp_path


def test_pdf_marcado_nao_e_reaberto(pasta_imagens):
    pdf = pasta_imagens / "900_INSP1.pdf"
    pdf.write_bytes(b"%PDF-1.4 ficticio")
    assert im._ja_verificado(str(pdf)) is False
    im.marcar_verificados([str(pdf)])
    assert im._ja_verificado(str(pdf)) is True


def test_pdf_alterado_e_reaberto(pasta_imagens):
    """A assinatura leva tamanho e data: PDF baixado de novo é relido."""
    pdf = pasta_imagens / "900_INSP1.pdf"
    pdf.write_bytes(b"%PDF-1.4 ficticio")
    im.marcar_verificados([str(pdf)])
    pdf.write_bytes(b"%PDF-1.4 outro conteudo, maior")
    assert im._ja_verificado(str(pdf)) is False


def test_lista_corrompida_usa_a_copia_reserva(pasta_imagens):
    """No Banco B a lista sumiu e 21.803 PDFs foram reabertos (2h10):
    agora a gravação anterior vira reserva."""
    im._salvar_verificados({"a.pdf": "2:10:1"})
    im._salvar_verificados({"a.pdf": "2:10:1", "b.pdf": "2:20:2"})
    (pasta_imagens / "_pdfs_verificados.json").write_text("corrompido", encoding="utf-8")
    assert im._carregar_verificados() == {"a.pdf": "2:10:1"}


def test_lista_que_nao_e_dicionario_e_ignorada(pasta_imagens):
    (pasta_imagens / "_pdfs_verificados.json").write_text(json.dumps(["a.pdf"]), encoding="utf-8")
    assert im._carregar_verificados() == {}


@pytest.mark.parametrize("valor, esperado", [("4", 4), ("0", multiprocessing.cpu_count()), ("abc", multiprocessing.cpu_count())])
def test_numero_de_processos(monkeypatch, valor, esperado):
    monkeypatch.setenv("IMAGENS_PROCESSOS", valor)
    assert im._num_processos() == esperado
