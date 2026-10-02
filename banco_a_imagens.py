"""
Extrai fotos dos laudos PDF do Banco A já baixados, salvando em data/imagens/.

Uso: python banco_a_imagens.py

Roda sozinho (passa por todos os PDFs de data/laudos/) e também é
chamado automaticamente pelo banco_a_extractor.py a cada execução - ou
seja, quem roda o extractor já ganha as imagens junto, sem precisar
rodar este arquivo separado.

Nunca regrava nem renomeia uma imagem que já existe em disco - só
acrescenta o que falta. Guarda em data/imagens/_pdfs_verificados.json os
PDFs que já foram lidos pela versão atual da lógica, pra não reabri-los.
Se quiser forçar a reextração de tudo, apague a pasta data/imagens/
inteira (ou suba VERSAO_LOGICA_IMAGENS).

Duas extrações, pelo mesmo princípio: no "RELATÓRIO FOTOGRÁFICO" cada
foto tem uma legenda embaixo, escrita à mão pelo vistoriador - por isso
ela varia bastante de laudo pra laudo ("Fachada", "FACHADA", "Fachada
do imovel avaliado"...).

1. Fachada (extrair_imagem_fachada) - pega a primeira legenda que
   começa com "fachada" em qualquer página, ignorando fachada de
   condomínio/prédio quando existe a do próprio imóvel. Salva como
   <nome_do_pdf>_img.<ext>. (Mesma regra validada nos laudos reais do
   Banco A desde a primeira versão deste arquivo.)

2. Todas as fotos do relatório fotográfico (extrair_fotos_relatorio) -
   percorre as páginas do RELATÓRIO FOTOGRÁFICO em ordem de leitura e,
   pra CADA foto, lê a legenda logo abaixo dela e classifica num label
   (CATEGORIAS_FOTO: sala, quarto, banheiro, cozinha, varanda, garagem,
   quintal, piscina, salão de festas, playground, academia, portaria,
   elevador, área comum, entorno, croqui, mapa...). O label vai no nome
   do arquivo: <nome_do_pdf>_<label>.<ext> pra primeira foto de cada
   label, <nome_do_pdf>_<label>_2.<ext>, _3... pras seguintes. Legenda
   que não cai em label nenhum vira "outro" com a legenda embutida no
   nome (laudo_X_outro_1_closet.jpg); foto sem legenda embaixo vira
   "sem_legenda". Foto repetida no mesmo laudo (mesmos bytes) sai uma
   vez só, e a fachada já salva nunca é regravada com outro nome.
   Só nas páginas do RELATÓRIO FOTOGRÁFICO: laudo sem essa seção (Laudo
   Eletrônico, sem vistoria) fica só com a fachada.

Mesma lógica do banco_b_imagens.py do pipeline-laudos-banco-b (pedido do
usuário, 01/10/2026: todas as fotos, inclusive áreas comuns e de lazer
do prédio, cada uma com seu label), sem as regras que só existem no
layout do Banco B (foto da capa como fachada, detecção do laudo
digital, checkboxes). Ainda não conferido em PDF real do Banco A - rodar o
testar_amostra.py antes da base toda.
"""

import glob
import hashlib
import json
import multiprocessing
import os
import shutil
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import pymupdf

PASTA_SCRIPT = os.path.dirname(os.path.abspath(__file__))
PASTA_LAUDOS = os.path.join(PASTA_SCRIPT, "data", "laudos")
PASTA_IMAGENS = os.path.join(PASTA_SCRIPT, "data", "imagens")
LOG_DIR = os.path.join(PASTA_SCRIPT, "logs")


class Tee:
    """Escreve simultaneamente no terminal e num arquivo de log. Duplicada
    aqui (em vez de importar de banco_a_extractor.py) porque o extractor
    já importa deste arquivo - importar de volta criaria import circular."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, dado):
        for s in self.streams:
            s.write(dado)

    def flush(self):
        for s in self.streams:
            s.flush()

# o logo do Banco A/Plataforma também é uma imagem na página - ignora
# qualquer coisa pequena demais pra ser uma foto de verdade.
LADO_MINIMO_FOTO = 100  # em pontos do PDF
# imagem que ocupa a página quase inteira é a página escaneada, não uma foto
FRACAO_PAGINA_ESCANEADA = 0.8
# a legenda fica logo abaixo da foto: até esta distância (em pontos) entre
# a borda de baixo da foto e o topo da linha de texto. No Banco A a legenda
# fica numa altura FIXA da página (dois quadros por página) e a foto é
# centralizada no quadro - foto deitada fica mais baixa e mais longe da
# legenda. Medido em 1.067 fotos de 36 laudos reais: 9 a 70 pontos (o 45
# que veio do Banco B deixava de fora as deitadas). 100 dá folga pra
# foto panorâmica.
DISTANCIA_MAXIMA_LEGENDA = 100

# Labels das fotos do relatório fotográfico, na ordem em que são testados:
# label -> palavras-chave (sem acento, minúsculo - ver _normalizar). A
# legenda é classificada em duas passadas: primeiro pelo COMEÇO dela
# ("Sala de estar" -> sala), depois pela palavra-chave que aparecer
# PRIMEIRO em qualquer posição ("Vista do quarto" -> quarto). A ordem da
# lista desempata quando duas casam no mesmo lugar - por isso o que é
# área comum vem antes de "sala" ("Sala de jogos", "Salão de festas"
# começam com "sala"). Pra acrescentar ou ajustar um label, é só mexer
# aqui; o que não cair em nenhum vira "outro", com a legenda no nome do
# arquivo, e o testar_amostra.py lista as legendas que viraram "outro".
# Os sinônimos abreviados/errados ("bh social", "a de servico",
# "dispensa", "cavalete", "ident numerica", "geo"...) vieram das legendas
# reais de 36 laudos do Banco A que caíam em "outro".
CATEGORIAS_FOTO = [
    # áreas comuns e lazer do prédio/condomínio
    ("salao_festas", ("salao de festa", "salao festa", "salao social", "salao de evento")),
    ("playground", ("playground", "parquinho", "brinquedoteca", "espaco kids")),
    ("academia", ("academia", "fitness", "sala de ginastica", "sala ginastica", "espaco fitness")),
    ("piscina", ("piscina",)),
    ("quadra", ("quadra",)),
    ("churrasqueira", ("churrasqueira", "area gourmet", "espaco gourmet", "gourmet")),
    ("portaria", ("portaria", "guarita", "recepcao", "hall de entrada", "hall social", "entrada do predio",
                  "entrada do condominio")),
    ("elevador", ("elevador",)),
    ("area_comum", ("area comum", "area de lazer", "lazer", "sala de jogo", "sala jogo", "salao de jogo",
                    "jardim do condominio", "condominio", "predio", "edificio", "torre", "bicicletario",
                    "pet place", "coworking", "sauna", "spa", "deck", "praca", "pista", "uso comum", "convivencia")),
    # fora do imóvel
    ("entorno", ("rua", "vizinhanca", "vizinho", "vizinha", "entorno", "via de acesso", "logradouro",
                 "sala vizinha", "imovel vizinho", "calcada")),
    # cômodos do imóvel
    ("sala", ("sala", "living", "estar", "jantar")),
    ("quarto", ("quarto", "dormitorio", "suite")),
    ("banheiro", ("banheiro", "lavabo", "wc", "bwc", "sanitario", "toalete", "toilet", "banho", "bh", "b social")),
    ("cozinha", ("cozinha", "copa")),
    ("area_servico", ("area de servico", "area servico", "lavanderia", "a de servico")),
    ("varanda", ("varanda", "sacada", "terraco")),
    ("garagem", ("garagem", "vaga", "vagas", "estacionamento")),
    ("quintal", ("quintal", "jardim", "area externa", "area livre", "fundos", "pomar", "edicula", "lateral",
                 "patio", "recuo")),
    ("escritorio", ("escritorio", "home office")),
    ("closet", ("closet",)),
    ("despensa", ("despensa", "dispensa", "deposito")),
    ("corredor", ("corredor", "hall", "circulacao")),
    ("escada", ("escada",)),
    ("telhado", ("telhado", "cobertura", "laje", "forro")),
    ("medidor", ("medidor", "relogio", "padrao de luz", "padrao de energia", "hidrometro", "caixa de luz",
                 "quadro de luz", "quadro de energia",
                 "cavalete", "energia", "luz", "agua")),
    # desenhos e localização
    ("croqui", ("croqui", "planta", "layout")),
    ("mapa", ("mapa", "localizacao", "vista aerea", "satelite", "amostra", "amostras", "geo", "geoportal")),
    ("numero", ("numero", "placa", "identificacao", "numeracao", "ident", "id ")),
    ("fachada", ("fachada", "frente do imovel", "frontal")),
]
LABEL_OUTRO = "outro"
LABEL_SEM_LEGENDA = "sem_legenda"

# os cinco cômodos da versão anterior (só a primeira foto de cada) -
# mantidos pro testar_amostra.py e pela compatibilidade com o Banco B
CATEGORIAS_COMODO = {
    "sala": ("sala",),
    "quarto": ("quarto", "dormitorio", "suite"),
    "banheiro": ("banheiro", "lavabo", "wc"),
    "cozinha": ("cozinha",),
    "area_servico": ("area de servico",),
}

_RE_NUMERACAO_LEGENDA = re.compile(r"^(?:foto|imagem|fig\.?|figura)?\s*\d+\s*[-:.)]*\s*")
# rodapé de página - inclui o "powered by Plataforma" do Banco A, que fica
# abaixo do quadro da esquerda e viraria legenda de foto sem legenda
_RE_RODAPE = re.compile(r"^(?:pag(?:ina)?\.?\s*\d+(?:\s*(?:/|de)\s*\d+)?|\d+\s*(?:/|de)\s*\d+|powered by|plataforma)$")


def _normalizar(txt):
    """minúsculo e sem acento, pra comparar legenda sem depender de como
    o vistoriador digitou ("Área" vs "Area", "á" vs "a")."""
    sem_acento = unicodedata.normalize('NFKD', txt).encode('ascii', 'ignore').decode('ascii')
    return sem_acento.lower().strip()


def classificar_legenda(legenda):
    """Label (de CATEGORIAS_FOTO) de uma legenda, LABEL_OUTRO se nenhuma
    palavra-chave casar, LABEL_SEM_LEGENDA se não houver legenda."""
    if not legenda or not legenda.strip():
        return LABEL_SEM_LEGENDA
    texto = _RE_NUMERACAO_LEGENDA.sub("", _normalizar(legenda)).strip(" -:.")
    if not texto:
        return LABEL_OUTRO
    # 1) pelo começo da legenda, na ordem da lista
    for label, chaves in CATEGORIAS_FOTO:
        if any(texto.startswith(c) for c in chaves):
            return label
    # 2) pela palavra-chave que aparecer primeiro na legenda
    melhor = None
    for ordem, (label, chaves) in enumerate(CATEGORIAS_FOTO):
        for c in chaves:
            m = re.search(r"(?<![a-z0-9])" + re.escape(c) + r"(?![a-z0-9])", texto)
            if m and (melhor is None or (m.start(), ordem) < melhor[0]):
                melhor = ((m.start(), ordem), label)
    return melhor[1] if melhor else LABEL_OUTRO


def _slug(legenda, maximo=30):
    """Legenda como pedaço de nome de arquivo: só letras, números e hífen."""
    texto = re.sub(r"[^a-z0-9]+", "-", _normalizar(legenda)).strip("-")
    return (texto[:maximo].rstrip("-")) or "sem-texto"


def _sufixo_arquivo(label, n, legenda):
    """Sufixo do nome do arquivo da n-ésima foto (1 = primeira) de um label.
    Primeira foto sem número (é o nome que já existia antes), depois _2,
    _3... Fachada começa em _2 porque a primeira é o _img. "outro" leva
    sempre o número e a legenda."""
    if label == LABEL_OUTRO:
        return f"outro_{n}_{_slug(legenda)}"
    if label == "fachada":
        return f"fachada_{n + 1}"
    return label if n == 1 else f"{label}_{n}"


def _label_do_sufixo(sufixo):
    """Inverso de _sufixo_arquivo (pra contar o que já existe na pasta)."""
    if sufixo == "img":
        return "fachada"
    if sufixo.startswith("outro_"):
        return LABEL_OUTRO
    return re.sub(r"_\d+$", "", sufixo)


def _imagem_existente(pasta_destino, nome_base, sufixo):
    """Caminho de uma imagem <nome_base>_<sufixo>.* já salva em
    pasta_destino, se existir (a extensão varia conforme o formato
    original da foto no PDF, por isso usa glob em vez de nome exato)."""
    encontrados = glob.glob(os.path.join(pasta_destino, f"{nome_base}_{sufixo}.*"))
    return encontrados[0] if encontrados else None


def _arquivos_do_laudo(pasta_destino, nome_base, arquivos=None):
    """{sufixo: nome do arquivo} das imagens já salvas deste laudo.
    `arquivos` = {nome sem extensão: nome do arquivo} da pasta inteira,
    quando quem chama já listou a pasta (o lote lista uma vez só)."""
    prefixo = nome_base + "_"
    if arquivos is None:
        nomes = [os.path.basename(c) for c in glob.glob(os.path.join(pasta_destino, prefixo + "*"))]
        arquivos = {os.path.splitext(n)[0]: n for n in nomes}
    return {stem[len(prefixo):]: nome for stem, nome in arquivos.items() if stem.startswith(prefixo)}


def _linhas_de_texto(page):
    """Agrupa as palavras da página por linha, pra conseguir ler a legenda
    inteira ('Fachada condomínio' são duas palavras na mesma linha).

    As páginas do relatório fotográfico do Banco A são páginas "deitadas"
    (rotação de 90° no PDF), e o PDF guarda as posições como se a página
    estivesse em pé: a legenda que aparece EMBAIXO da foto na tela fica do
    LADO dela nessas coordenadas, e nenhuma legenda era achada. Por isso
    textos e fotos passam pela rotation_matrix da página (posição como
    aparece na tela) - em página sem rotação ela não muda nada."""
    matriz = page.rotation_matrix
    linhas = defaultdict(list)
    for x0, y0, x1, y1, palavra, bloco, linha, _n in page.get_text("words"):
        r = pymupdf.Rect(x0, y0, x1, y1) * matriz
        linhas[(bloco, linha)].append((r.x0, r.y0, r.x1, r.y1, palavra))
    return linhas


def _fotos_da_pagina(page):
    """Retorna [(xref, retângulo)] das imagens grandes o suficiente pra
    serem fotos (descarta logo e ícones). Retângulo na posição da tela,
    igual ao texto (ver _linhas_de_texto)."""
    matriz = page.rotation_matrix
    fotos = []
    for imagem in page.get_images(full=True):
        xref = imagem[0]
        for rect in page.get_image_rects(xref):
            rect = rect * matriz
            if rect.width >= LADO_MINIMO_FOTO and rect.height >= LADO_MINIMO_FOTO:
                fotos.append((xref, rect))
    return fotos


def _legenda_da_foto(rect, linhas):
    """Texto da linha logo abaixo da foto (a legenda): a linha mais próxima
    cuja borda de cima fica até DISTANCIA_MAXIMA_LEGENDA pontos abaixo da
    borda de baixo da foto e que se sobrepõe a ela na horizontal. Rodapé
    de página ("Página 3 de 10") não conta. None se não houver."""
    melhor = None
    for palavras in linhas.values():
        x0 = min(p[0] for p in palavras)
        x1 = max(p[2] for p in palavras)
        y0 = min(p[1] for p in palavras)
        if x1 <= rect.x0 or x0 >= rect.x1:
            continue
        distancia = y0 - rect.y1
        if distancia < -3 or distancia > DISTANCIA_MAXIMA_LEGENDA:
            continue
        texto = " ".join(p[4] for p in sorted(palavras, key=lambda p: p[0])).strip()
        if not texto or _RE_RODAPE.match(_normalizar(texto)) or "relatorio fotografico" in _normalizar(texto):
            continue
        if melhor is None or distancia < melhor[0]:
            melhor = (distancia, texto)
    return melhor[1] if melhor else None


def _paginas_de_comodo(documento):
    """Páginas onde procurar as fotos do relatório: só as que têm o título
    "RELATÓRIO FOTOGRÁFICO" (a seção de fotos da vistoria). Devolve
    (paginas, eh_digital) como no Banco B; aqui eh_digital é sempre
    False - laudo sem a seção (Laudo Eletrônico) simplesmente não tem
    página pra procurar, e nada é salvo além da fachada."""
    return [pg for pg in documento if "relatorio fotografico" in _normalizar(pg.get_text())], False


def _legendas_de_fachada(documento):
    """Todas as legendas que começam com 'fachada', em ordem de página."""
    candidatos = []
    for page in documento:
        fotos = _fotos_da_pagina(page)
        if not fotos:
            continue
        for palavras in _linhas_de_texto(page).values():
            legenda = " ".join(p[4] for p in palavras).strip()
            if legenda.lower().startswith("fachada"):
                candidatos.append((page, fotos, palavras, legenda))
    return candidatos


def extrair_imagem_fachada(pdf_path, pasta_destino=PASTA_IMAGENS, documento=None):
    """Salva a foto de fachada do laudo em pasta_destino. Devolve
    (caminho_do_arquivo, legenda_usada) - legenda vem None se a imagem já
    existia (pulou sem reabrir o PDF) - ou (None, None) se o laudo não
    tiver nenhuma foto de fachada."""
    nome_base = os.path.splitext(os.path.basename(pdf_path))[0]

    existente = _imagem_existente(pasta_destino, nome_base, "img")
    if existente:
        return existente, None

    # `documento` já aberto (lote abre uma vez só pra fachada e cômodos)
    abriu_aqui = documento is None
    if abriu_aqui:
        documento = pymupdf.open(pdf_path)
    try:
        candidatos = _legendas_de_fachada(documento)
        if candidatos:
            # "Fachada condomínio"/"Fachada do prédio" é a fachada do
            # empreendimento, não a do imóvel avaliado - só vale quando não
            # existe nenhuma outra (caso de apartamento, em que a fachada do
            # prédio é a foto certa mesmo).
            proprias = [c for c in candidatos if "condom" not in c[3].lower()]
            page, fotos, palavras, legenda = (proprias or candidatos)[0]

            centro_x = sum((p[0] + p[2]) / 2 for p in palavras) / len(palavras)
            centro_y = sum((p[1] + p[3]) / 2 for p in palavras) / len(palavras)

            xref_escolhido, _ = min(
                fotos,
                key=lambda foto: (
                    ((foto[1].x0 + foto[1].x1) / 2 - centro_x) ** 2
                    + ((foto[1].y0 + foto[1].y1) / 2 - centro_y) ** 2
                ),
            )
        else:
            return None, None

        imagem = documento.extract_image(xref_escolhido)
        extensao = "jpg" if imagem["ext"] == "jpeg" else imagem["ext"]
        os.makedirs(pasta_destino, exist_ok=True)
        destino = os.path.join(pasta_destino, f"{nome_base}_img.{extensao}")
        with open(destino, "wb") as arquivo:
            arquivo.write(imagem["image"])
        return destino, legenda
    finally:
        if abriu_aqui:
            documento.close()


def _tirar_da_pasta(caminho, pasta_destino):
    """Em vez de apagar, move pra <pasta_destino>/_removidas/ - dá pra
    conferir e desfazer. Devolve o caminho novo."""
    pasta_removidas = os.path.join(pasta_destino, "_removidas")
    os.makedirs(pasta_removidas, exist_ok=True)
    novo = os.path.join(pasta_removidas, os.path.basename(caminho))
    base, extensao = os.path.splitext(novo)
    n = 1
    while os.path.exists(novo):  # já tirada antes com o mesmo nome: não sobrescreve
        n += 1
        novo = f"{base}_{n}{extensao}"
    shutil.move(caminho, novo)
    return novo


def _md5(dados):
    return hashlib.md5(dados).hexdigest()


def _md5_arquivo(caminho):
    with open(caminho, "rb") as f:
        return _md5(f.read())


def extrair_fotos_relatorio(pdf_path, pasta_destino=PASTA_IMAGENS, documento=None, arquivos=None,
                            removidas=None, revisar=(), trocadas=None, fachada_salva=None):
    """Salva TODAS as fotos das páginas do RELATÓRIO FOTOGRÁFICO, cada uma
    com o label da legenda no nome (ver CATEGORIAS_FOTO e _sufixo_arquivo).
    Devolve uma lista de registros, na ordem do laudo:
        {"label", "legenda", "caminho", "nova"}  (nova=False: já existia)

    Nunca regrava nem renomeia o que já está na pasta: foto cujos bytes
    já estão salvos (com qualquer nome) é só reconhecida, e foto nova ganha
    o próximo número livre do seu label. Rodar de novo não muda nada.

    `revisar`: labels da versão antiga cuja PRIMEIRA foto deve ser
    escolhida de novo pela regra atual. Se a regra atual escolher a mesma
    foto, nada muda; senão a antiga vai pra _removidas/ e a nova é salva.
    Cada troca entra em `trocadas` como (label, caminho antigo, caminho
    novo ou None, legenda nova).

    `fachada_salva`: caminho do _img que extrair_imagem_fachada acabou de
    gravar neste mesmo passo (ainda não está em `arquivos`), pra ele não
    sair de novo como "fachada_2"."""
    nome_base = os.path.splitext(os.path.basename(pdf_path))[0]
    abriu_aqui = documento is None
    if abriu_aqui:
        documento = pymupdf.open(pdf_path)
    try:
        paginas, eh_digital = _paginas_de_comodo(documento)
        existentes = _arquivos_do_laudo(pasta_destino, nome_base, arquivos)
        if fachada_salva and "img" not in existentes and os.path.exists(fachada_salva):
            existentes["img"] = os.path.basename(fachada_salva)
        if eh_digital:
            return []

        # o que já está salvo deste laudo, pelos bytes: assim uma foto já
        # extraída é reconhecida mesmo se o label dela mudar de versão
        md5_existentes = {}
        em_revisao = {}  # label -> (caminho antigo, md5)
        for sufixo, nome in existentes.items():
            caminho = os.path.join(pasta_destino, nome)
            try:
                h = _md5_arquivo(caminho)
            except OSError:
                continue
            label = _label_do_sufixo(sufixo)
            if sufixo in revisar:  # só a primeira foto do label (sufixo sem número)
                em_revisao[label] = (caminho, h)
            else:
                md5_existentes[h] = caminho
        for sufixo in list(revisar):
            existentes.pop(sufixo, None)  # o nome fica livre pra escolha nova
        # a fachada (_img) nunca é regravada com outro nome
        vistos = set(md5_existentes)
        # próximo número livre por label (contando o que já existe)
        contagem = Counter(_label_do_sufixo(s) for s in existentes if s not in revisar and s != "img")

        registros = [{"label": _label_do_sufixo(s), "legenda": None,
                      "caminho": os.path.join(pasta_destino, n), "nova": False}
                     for s, n in sorted(existentes.items()) if s not in revisar and s != "img"]
        mantidas = set()
        for page in paginas:
            fotos = _fotos_da_pagina(page)
            if not fotos:
                continue
            linhas = _linhas_de_texto(page)
            area_pagina = page.rect.width * page.rect.height
            # ordem de leitura: de cima pra baixo, da esquerda pra direita
            for xref, rect in sorted(fotos, key=lambda f: (round(f[1].y0 / 10), f[1].x0)):
                if rect.width * rect.height >= FRACAO_PAGINA_ESCANEADA * area_pagina:
                    continue  # página escaneada inteira, não é uma foto
                imagem = documento.extract_image(xref)
                h = _md5(imagem["image"])
                if h in vistos:
                    continue  # mesma foto repetida, ou já salva (fachada inclusive)
                legenda = _legenda_da_foto(rect, linhas)
                label = classificar_legenda(legenda)
                if label in em_revisao and em_revisao[label][1] == h:
                    # a regra atual escolhe a mesma foto de antes: fica como está
                    vistos.add(h)
                    mantidas.add(label)
                    contagem[label] += 1
                    registros.append({"label": label, "legenda": None,
                                      "caminho": em_revisao[label][0], "nova": False})
                    continue
                vistos.add(h)
                contagem[label] += 1
                sufixo = _sufixo_arquivo(label, contagem[label], legenda)
                if sufixo in existentes:
                    # nome já usado por outra foto (não deveria acontecer:
                    # a contagem parte do que existe) - pula pro próximo livre
                    while sufixo in existentes:
                        contagem[label] += 1
                        sufixo = _sufixo_arquivo(label, contagem[label], legenda)
                extensao = "jpg" if imagem["ext"] == "jpeg" else imagem["ext"]
                os.makedirs(pasta_destino, exist_ok=True)
                destino = os.path.join(pasta_destino, f"{nome_base}_{sufixo}.{extensao}")
                movida = None
                if label in em_revisao and label not in mantidas:
                    # a primeira foto desse label mudou: a antiga sai ANTES de
                    # gravar (pode ter o mesmo nome) e vai pra _removidas/
                    movida = _tirar_da_pasta(em_revisao.pop(label)[0], pasta_destino)
                with open(destino, "wb") as arquivo:
                    arquivo.write(imagem["image"])
                existentes[sufixo] = os.path.basename(destino)
                registros.append({"label": label, "legenda": legenda or "", "caminho": destino, "nova": True})
                if movida and trocadas is not None:
                    trocadas.append((label, movida, destino, legenda))
        # label em revisão que a regra atual não acha mais
        for label, (antiga, _h) in em_revisao.items():
            if label in mantidas:
                continue
            movida = _tirar_da_pasta(antiga, pasta_destino)
            if trocadas is not None:
                trocadas.append((label, movida, None, None))
        return registros
    finally:
        if abriu_aqui:
            documento.close()


def extrair_imagens_comodos(pdf_path, categorias=CATEGORIAS_COMODO, pasta_destino=PASTA_IMAGENS, **kw):
    """Compatibilidade com quem usava a versão antiga: {label: (caminho,
    legenda)} da PRIMEIRA foto de cada label. Hoje é só um atalho sobre
    extrair_fotos_relatorio - legenda vem None pra foto que já existia."""
    primeira = {}
    for r in extrair_fotos_relatorio(pdf_path, pasta_destino=pasta_destino, **kw):
        primeira.setdefault(r["label"], (r["caminho"], r["legenda"] if r["nova"] else None))
    return primeira


# PDFs que já foram abertos pela versão atual e não têm mais nada pra
# extrair - sem isso, todo laudo era reaberto e lido inteiro a cada
# execução: horas em dezenas de milhares de PDFs. Mudou a lógica de
# escolha das fotos? Sobe VERSAO_LOGICA_IMAGENS e todos são reabertos.
#   1: fachada + primeira foto de 5 cômodos
#   2: todas as fotos do relatório, com label
VERSAO_LOGICA_IMAGENS = 2
# o que cada versão mudou: PDF verificado numa versão anterior só é
# reaberto se estiver sem alguma dessas imagens (o resto não muda)
MUDOU_NA_VERSAO = {}
# a partir desta versão saem TODAS as fotos do relatório: PDF verificado
# antes dela é reaberto sempre (não dá pra saber pelos arquivos o que falta)
VERSAO_TODAS_AS_FOTOS = 2
# e estas, mesmo JÁ salvas, são escolhidas de novo uma vez pela regra
# atual (ver `revisar` em extrair_fotos_relatorio) - só troca se mudar
REVISAR_NA_VERSAO = {}
# cômodo com a MESMA foto da fachada salva é escolhido de novo até esta
# versão (nunca aconteceu no Banco A; 0 desliga)
VERSAO_REVISA_IGUAL_FACHADA = 0
CACHE_VERIFICADOS = os.path.join(PASTA_IMAGENS, "_pdfs_verificados.json")


def _assinatura(caminho_pdf):
    info = os.stat(caminho_pdf)
    return f"{VERSAO_LOGICA_IMAGENS}:{info.st_size}:{int(info.st_mtime)}"


CACHE_RESERVA = CACHE_VERIFICADOS + ".reserva"


def _ler_json(caminho):
    with open(caminho, encoding="utf-8") as f:
        dados = json.load(f)
    if not isinstance(dados, dict):
        raise ValueError("não é um dicionário")
    return dados


def _carregar_verificados(avisar=False):
    """No Banco B (29/09/2026) a lista chegou vazia (causa não descoberta
    - nenhum script apaga o arquivo) e a etapa reabriu 21.803 PDFs: 2h10
    em vez de segundos. Por isso há uma cópia reserva, e sumir a lista
    vira aviso."""
    for caminho in (CACHE_VERIFICADOS, CACHE_RESERVA):
        try:
            dados = _ler_json(caminho)
        except (OSError, ValueError) as e:
            if avisar and os.path.exists(caminho):
                print(f"[AVISO] Não consegui ler {os.path.basename(caminho)} ({e}).")
            continue
        if avisar and caminho == CACHE_RESERVA:
            print(f"[AVISO] Lista de PDFs verificados ausente/ilegível - usando a cópia reserva "
                  f"({len(dados)} PDFs).")
        return dados
    if avisar and os.path.isdir(PASTA_IMAGENS) and len(os.listdir(PASTA_IMAGENS)) > 100:
        print("[AVISO] Lista de PDFs verificados NÃO encontrada, mas já existem imagens salvas - "
              "todos os PDFs vão ser reabertos (pode levar horas).", flush=True)
    return {}


def _salvar_verificados(verificados):
    os.makedirs(PASTA_IMAGENS, exist_ok=True)
    temporario = CACHE_VERIFICADOS + ".tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(verificados, f)
    # a versão anterior (se estiver boa) vira a reserva antes da troca
    try:
        _ler_json(CACHE_VERIFICADOS)
        shutil.copy2(CACHE_VERIFICADOS, CACHE_RESERVA)
    except (OSError, ValueError):
        pass
    os.replace(temporario, CACHE_VERIFICADOS)


_verificados_do_processo = None


def marcar_verificados(caminhos_pdf):
    """Põe na lista de verificados os PDFs que o banco_a_extractor
    acabou de processar (extrair_imagens_do_laudo devolveu True) - sem
    isso a etapa de imagens do pipeline reabria todos eles de novo.
    Chamar num processo só (o principal do extrator), depois do lote."""
    global _verificados_do_processo
    if not caminhos_pdf:
        return
    verificados = _carregar_verificados()
    for caminho in caminhos_pdf:
        try:
            verificados[os.path.basename(caminho)] = _assinatura(caminho)
        except OSError:
            pass
    _salvar_verificados(verificados)
    _verificados_do_processo = verificados  # a cópia deste processo não fica velha


# processos em paralelo no lote (um por núcleo do processador). Pra
# usar menos (ex.: deixar o PC livre pra outra coisa):
#   $env:IMAGENS_PROCESSOS="4"
def _num_processos():
    try:
        valor = int(os.getenv("IMAGENS_PROCESSOS") or 0)
    except ValueError:
        valor = 0
    return valor if valor > 0 else multiprocessing.cpu_count()


def _ja_verificado(caminho_pdf):
    """True se a lista de verificados diz que este PDF já foi lido pela
    versão atual. A lista é carregada uma vez por processo (o extrator
    chama isto de dentro dos workers)."""
    global _verificados_do_processo
    if _verificados_do_processo is None:
        _verificados_do_processo = _carregar_verificados()
    try:
        return _verificados_do_processo.get(os.path.basename(caminho_pdf)) == _assinatura(caminho_pdf)
    except OSError:
        return False


def extrair_imagens_do_laudo(pdf_path):
    """Fachada + todas as fotos do relatório abrindo o PDF uma vez só (e
    nem abre se a lista de verificados diz que já foi feito). Usado pelo
    banco_a_extractor a cada PDF. Devolve True quando leu o PDF agora
    (o extrator então o põe na lista de verificados)."""
    if _ja_verificado(pdf_path):
        return False
    with pymupdf.open(pdf_path) as documento:
        fachada, _legenda = extrair_imagem_fachada(pdf_path, documento=documento)
        extrair_fotos_relatorio(pdf_path, documento=documento, fachada_salva=fachada)
    return True


def _mesmo_conteudo(caminho_a, caminho_b):
    with open(caminho_a, "rb") as a, open(caminho_b, "rb") as b:
        return a.read() == b.read()


def _revisar_da_versao(versao, nome_base, arquivos):
    """Sufixos já salvos que um PDF verificado na `versao` (0 = nunca)
    precisa escolher de novo: os de REVISAR_NA_VERSAO e, até a
    VERSAO_REVISA_IGUAL_FACHADA, cômodo com a mesma foto da fachada.
    `arquivos`: {nome sem extensão: nome do arquivo} de data/imagens."""
    revisar = set()
    for v, categorias in REVISAR_NA_VERSAO.items():
        if versao < v:
            revisar |= {c for c in categorias if f"{nome_base}_{c}" in arquivos}
    fachada = arquivos.get(f"{nome_base}_img")
    if versao < VERSAO_REVISA_IGUAL_FACHADA and fachada:
        fachada = os.path.join(PASTA_IMAGENS, fachada)
        tamanho = os.path.getsize(fachada)
        for categoria in CATEGORIAS_COMODO:
            nome = arquivos.get(f"{nome_base}_{categoria}")
            if not nome or categoria in revisar:
                continue
            caminho = os.path.join(PASTA_IMAGENS, nome)
            # compara o tamanho antes (quase sempre difere) pra não ler tudo
            if os.path.getsize(caminho) == tamanho and _mesmo_conteudo(caminho, fachada):
                revisar.add(categoria)
    return revisar


def _processar_um_pdf(tarefa):
    """Roda num processo separado: fachada + fotos do relatório de um PDF,
    abrindo o arquivo uma vez só. Não imprime nada (o print de um processo
    filho não vai pro log) - devolve tudo pro processo principal."""
    caminho, revisar, arquivos = tarefa
    nome_pdf = os.path.basename(caminho)
    try:
        assinatura = _assinatura(caminho)
        removidas = []
        trocadas = []
        with pymupdf.open(caminho) as documento:
            fachada = extrair_imagem_fachada(caminho, documento=documento)
            fotos = extrair_fotos_relatorio(caminho, documento=documento, arquivos=arquivos,
                                            removidas=removidas, revisar=revisar, trocadas=trocadas,
                                            fachada_salva=fachada[0])
        return {"nome": nome_pdf, "assinatura": assinatura, "fachada": fachada,
                "fotos": fotos, "removidas": removidas, "trocadas": trocadas, "erro": None}
    except Exception as e:
        return {"nome": nome_pdf, "erro": str(e) or type(e).__name__}


def processar_em_lote():
    if not os.path.exists(PASTA_LAUDOS):
        print(f"Pasta não encontrada: {PASTA_LAUDOS}")
        return

    pdfs = sorted(f for f in os.listdir(PASTA_LAUDOS) if f.endswith(".pdf"))
    print(f"Total de PDFs encontrados: {len(pdfs)}")
    if not pdfs:
        return

    novas = puladas = sem_fachada = erros = ja_verificados = 0
    trocadas = []
    verificados = _carregar_verificados(avisar=True)
    fotos_novas = Counter()
    fotos_existentes = Counter()
    legendas_outro = Counter()

    # lista a pasta de imagens UMA vez e passa pros processos (antes eram
    # várias buscas no disco por PDF)
    arquivos = {}
    if os.path.isdir(PASTA_IMAGENS):
        arquivos = {os.path.splitext(f)[0]: f for f in os.listdir(PASTA_IMAGENS)}
    salvas = arquivos.keys()

    a_processar = []
    a_revisar = 0
    for nome_pdf in pdfs:
        caminho = os.path.join(PASTA_LAUDOS, nome_pdf)
        nome_base = os.path.splitext(nome_pdf)[0]
        versao, mesmo_arquivo, assinatura = 0, False, None
        try:
            assinatura = _assinatura(caminho)
            anterior = verificados.get(nome_pdf, "")
            if anterior == assinatura:
                ja_verificados += 1
                continue
            v, _, resto = anterior.partition(":")
            if v.isdigit():
                versao = int(v)
                mesmo_arquivo = resto == assinatura.partition(":")[2]
        except OSError:
            pass
        revisar = _revisar_da_versao(versao, nome_base, arquivos)
        if revisar:
            a_revisar += 1
        elif mesmo_arquivo and versao >= VERSAO_TODAS_AS_FOTOS:
            mudou = {sufixo for v, sufixos_v in MUDOU_NA_VERSAO.items()
                     if v > versao for sufixo in sufixos_v}
            if all(f"{nome_base}_{sufixo}" in salvas for sufixo in mudou):
                verificados[nome_pdf] = assinatura
                ja_verificados += 1
                continue
        # só os arquivos deste laudo vão pro processo (a pasta inteira
        # tem centenas de milhares de nomes)
        prefixo = nome_base + "_"
        do_laudo = {s: n for s, n in arquivos.items() if s.startswith(prefixo)} if arquivos else {}
        a_processar.append((caminho, tuple(sorted(revisar)), do_laudo))

    num_processos = min(_num_processos(), max(1, len(a_processar)))
    print(f"Já verificados antes: {ja_verificados} | a abrir: {len(a_processar)}, "
          f"{a_revisar} deles pra rever foto salva por versão antiga "
          f"({num_processos} processos em paralelo)")
    if len(a_processar) > 1000:
        print("  (primeira passada da versão que extrai todas as fotos do relatório: "
              "cada PDF é aberto uma vez - pode levar horas na base inteira)")

    if a_processar:
        with ProcessPoolExecutor(max_workers=num_processos) as executor:
            # map devolve na ordem da lista (log em ordem alfabética) e o
            # chunksize corta o vaivém entre processos
            resultados = executor.map(_processar_um_pdf, a_processar, chunksize=8)
            for i, r in enumerate(resultados, 1):
                nome_pdf = r["nome"]
                if i % 2000 == 0:
                    print(f"  ... {i}/{len(a_processar)} PDFs abertos", flush=True)
                    _salvar_verificados(verificados)
                if r["erro"]:
                    erros += 1
                    print(f"  [ERRO] {nome_pdf}: {r['erro']}")
                    continue

                destino, legenda = r["fachada"]
                if destino and legenda:
                    novas += 1
                    # mostra a legenda usada - como ela é digitada à mão pelo
                    # vistoriador, é o jeito de conferir se pegou a foto certa.
                    print(f"  {nome_pdf} -> {os.path.basename(destino)} (legenda: \"{legenda}\")")
                elif destino:
                    puladas += 1  # já existia
                else:
                    sem_fachada += 1
                    print(f"  [SEM FACHADA] {nome_pdf}: nenhuma legenda começando com 'Fachada'.")

                for label, antiga, nova, legenda_nova in r["trocadas"]:
                    trocadas.append(label)
                    if nova:
                        print(f"  [TROCADA] {nome_pdf} -> {os.path.basename(nova)} (legenda: \"{legenda_nova}\"); "
                              f"a antiga foi pra _removidas/{os.path.basename(antiga)}")
                    else:
                        print(f"  [TROCADA] {nome_pdf}: {label} tirada (a regra atual não acha foto "
                              f"desse cômodo) - foi pra _removidas/{os.path.basename(antiga)}")
                novas_do_pdf = [f for f in r["fotos"] if f["nova"]]
                for f in r["fotos"]:
                    (fotos_novas if f["nova"] else fotos_existentes)[f["label"]] += 1
                    if f["nova"] and f["label"] == LABEL_OUTRO:
                        legendas_outro[f["legenda"]] += 1
                if novas_do_pdf:
                    resumo = ", ".join(f"{os.path.basename(f['caminho'])} (\"{f['legenda']}\")"
                                       for f in novas_do_pdf[:6])
                    extra = f" e mais {len(novas_do_pdf) - 6}" if len(novas_do_pdf) > 6 else ""
                    print(f"  {nome_pdf}: {len(novas_do_pdf)} foto(s) nova(s): {resumo}{extra}")
                verificados[nome_pdf] = r["assinatura"]

    _salvar_verificados(verificados)
    print("-" * 60)
    print(f"  PDFs já verificados antes (nem abertos): {ja_verificados}")
    print(f"  Fachadas novas:            {novas}")
    print(f"  Fachadas já existentes:    {puladas}")
    print(f"  Sem foto de fachada:       {sem_fachada}")
    print("  Fotos do relatório por label (novas / já existiam):")
    for label in sorted(set(fotos_novas) | set(fotos_existentes), key=lambda l: -fotos_novas[l] - fotos_existentes[l]):
        print(f"    {label:<16} {fotos_novas[label]} / {fotos_existentes[label]}")
    if legendas_outro:
        print(f"  Legendas mais comuns que viraram \"{LABEL_OUTRO}\" (candidatas a label novo em CATEGORIAS_FOTO):")
        for legenda, n in legendas_outro.most_common(15):
            print(f"    {n:>5}  {legenda}")
    print(f"  Cômodos trocados (versão antiga): {len(trocadas)}"
          + (f" - {dict(sorted(Counter(trocadas).items()))}" if trocadas else ""))
    print(f"  Com erro:                  {erros}")
    print(f"  Pasta: {PASTA_IMAGENS}")
    print("-" * 60)


if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"imagens_{datetime.now():%Y%m%d_%H%M%S}.txt")
    log_file = open(log_path, "w", encoding="utf-8")
    stdout_original = sys.stdout
    sys.stdout = Tee(stdout_original, log_file)
    print(f"Log desta execução: {log_path}")
    inicio = datetime.now()
    try:
        processar_em_lote()
        print(f"Tempo total: {str(datetime.now() - inicio).split('.')[0]}")
    finally:
        sys.stdout = stdout_original
        log_file.close()
