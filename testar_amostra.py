"""Teste rápido: roda a extração (dados + imagens) numa amostra de laudos
do Banco A e aponta problema ANTES de rodar na base inteira.

Não grava nada no banco nem em data/imagens/ - as imagens vão pra uma
pasta temporária que é apagada no final. Pode rodar quantas vezes quiser.

Uso (na pasta do repositório):

    python testar_amostra.py

Quando rodar: depois de um `git pull` que mudou a extração (antes de
reprocessar tudo com FORCAR_REPROCESSAR) e depois de baixar meses novos
(antes de extrair) - laudo novo é onde layout novo aparece.

A amostra junta laudos sorteados da pasta com os baixados mais
recentemente (1/3 da amostra), porque foi em laudo novo que o layout
mudou da última vez (no Banco B). Padrão: 60 laudos, 1-2 minutos.
    $env:TESTE_QTD="150"      amostra maior
    $env:TESTE_SEMENTE="123"  repete o mesmo sorteio (o nº sai no relatório)

Dois tipos de checagem:
1. dados absurdos ou vazios (proposta, tipo, valor, área, endereço, texto
   com pedaço de outra coluna...) - as mesmas do checagens.py que o
   extrator aplica no lote antes de gravar;
2. imagens: cômodo igual à fachada, sala vinda de área comum, sem fachada,
   foto do relatório sem legenda, e quais legendas viraram "outro" (pra
   crescer a lista de labels do banco_a_imagens.py com dado real).

(O Banco B tem ainda a conferência contra uma segunda fonte dentro do
PDF - tabelas de cálculo e amostras. O laudo do Banco A não tem essas
seções no mesmo formato, então aqui ela não existe.)

Relatório em logs/teste_rapido_<data>.txt, com veredito no final.
"""

import hashlib
import multiprocessing
import os
import random
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime

import checagens
import banco_a_extractor as ie
import banco_a_imagens as ii

PASTA_SCRIPT = os.path.dirname(os.path.abspath(__file__))
PASTA_LAUDOS = os.path.join(PASTA_SCRIPT, "data", "laudos")
LOG_DIR = os.path.join(PASTA_SCRIPT, "logs")
QTD_PADRAO = 60
MAX_EXEMPLOS_POR_CHECAGEM = 8

# Checagem -> quanto dela é tolerável na amostra antes do veredito virar
# "não rode ainda". 0 = bug conhecido, não pode aparecer nenhuma vez.
LIMITES = {
    # imagens
    "cômodo com a mesma foto da fachada": 0,
    "sala vinda de área comum ou vizinha (salão de festas...)": 0,
    "cômodo já salvo em data/imagens igual à fachada salva": 0,
    "sem foto de fachada (laudo físico)": 0.10,
    # o Laudo Eletrônico não tem vistoria; se ele também não trouxer foto
    # de fachada, isso é normal e não pode barrar o veredito - só aparece
    # no relatório pra gente saber quantos são
    "sem foto de fachada (laudo eletrônico - pode ser normal)": 1.0,
    # foto do relatório sem legenda embaixo (layout diferente do esperado?)
    "laudo com foto do relatório sem legenda": 0.10,
    # legenda que não caiu em nenhum label de CATEGORIAS_FOTO - não é erro,
    # é lista pra crescer: o relatório mostra as legendas mais comuns
    "laudo com foto classificada como 'outro'": 1.0,
    # "LAUDO DE INSPEÇÃO" (só a vistoria, sem valor) salvo no lugar da
    # avaliação - o downloader não salva mais (ver eh_laudo_de_inspecao),
    # então qualquer um na pasta é sobra de versão antiga ou bug
    "Laudo de Inspeção salvo no lugar da avaliação": 0,
    # dados do laudo (mesmas do extrator - ver checagens.py)
    **checagens.LIMITES_DADOS,
}

# legendas de área comum do condomínio/unidade vizinha que já viraram
# "sala" no Banco B - fixo aqui (e não lido de banco_a_imagens) pra pegar
# o bug mesmo se a lista de lá for apagada
AREA_COMUM = ("salao de festa", "salao festa", "sala de jogo", "sala jogo", "sala vizinha")


def _hash(caminho):
    with open(caminho, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def analisar_pdf(caminho, pasta_imagens):
    """Roda a extração de um PDF e devolve (nome, modelo, problemas). Roda
    num processo separado - por isso anula aqui (e não no processo
    principal) a gravação de imagem que o extrator faz em data/imagens/,
    e extrai as imagens de novo numa pasta temporária."""
    ie.extrair_imagens_do_laudo = lambda *a, **k: None

    nome = os.path.basename(caminho)
    problemas = []

    resultado = ie.extrair_dados_pdf(caminho)
    if resultado["status"] == "inspecao":
        return nome, "?", [("Laudo de Inspeção salvo no lugar da avaliação",
                            "tire de data/laudos/ pro robô baixar a avaliação")]
    if resultado["status"] != "ok":
        motivo = resultado.get("mensagem") or "PDF sem texto (escaneado?)"
        return nome, "?", [("erro ao processar o PDF", motivo)]

    d = resultado["dados"]
    modelo = d.get("modelo_usado", "?")

    def p(checagem, detalhe=""):
        problemas.append((checagem, detalhe))

    # --- dados do laudo
    problemas.extend(checagens.problemas_dos_dados(d, resultado.get("nao_lidos", ())))

    # --- imagens (pasta temporária)
    try:
        fachada, _ = ii.extrair_imagem_fachada(caminho, pasta_imagens)
        fotos = ii.extrair_fotos_relatorio(caminho, pasta_destino=pasta_imagens, fachada_salva=fachada)
        if not fachada:
            p("sem foto de fachada (laudo físico)" if modelo == "fisico"
              else "sem foto de fachada (laudo eletrônico - pode ser normal)")
        hash_fachada = _hash(fachada) if fachada else None
        sem_legenda = [f for f in fotos if f["label"] == ii.LABEL_SEM_LEGENDA]
        outros = [f["legenda"] for f in fotos if f["label"] == ii.LABEL_OUTRO]
        if sem_legenda:
            p("laudo com foto do relatório sem legenda", f"{len(sem_legenda)} de {len(fotos)} foto(s)")
        if outros:
            p("laudo com foto classificada como 'outro'", "; ".join(repr(l) for l in outros[:5]))
        for f in fotos:
            categoria, arquivo, legenda = f["label"], f["caminho"], f["legenda"]
            if hash_fachada and categoria != "fachada" and _hash(arquivo) == hash_fachada:
                p("cômodo com a mesma foto da fachada", f"{categoria} (legenda {legenda!r})")
            if categoria == "sala" and ii._normalizar(legenda or "").startswith(AREA_COMUM):
                p("sala vinda de área comum ou vizinha (salão de festas...)", f"legenda {legenda!r}")
    except Exception as e:
        p("erro ao processar o PDF", f"imagens: {e}")

    # --- imagens já salvas na base (só lê): versão que salvou a fachada
    # como cômodo e nunca foi revista
    nome_base = os.path.splitext(nome)[0]
    fachada_salva = ii._imagem_existente(ii.PASTA_IMAGENS, nome_base, "img")
    if fachada_salva:
        hash_salva = _hash(fachada_salva)
        for categoria in ii.CATEGORIAS_COMODO:
            salvo = ii._imagem_existente(ii.PASTA_IMAGENS, nome_base, categoria)
            if salvo and _hash(salvo) == hash_salva:
                p("cômodo já salvo em data/imagens igual à fachada salva", categoria)

    return nome, modelo, problemas


def escolher_amostra(qtd, semente):
    """1/3 dos laudos baixados mais recentemente + o resto sorteado."""
    todos = [f for f in os.listdir(PASTA_LAUDOS) if f.lower().endswith(".pdf")]
    if not todos:
        return []
    qtd = min(qtd, len(todos))
    por_data = sorted(todos, key=lambda f: os.path.getmtime(os.path.join(PASTA_LAUDOS, f)), reverse=True)
    recentes = por_data[: qtd // 3]
    resto = [f for f in todos if f not in set(recentes)]
    sorteados = random.Random(semente).sample(resto, min(qtd - len(recentes), len(resto)))
    return [os.path.join(PASTA_LAUDOS, f) for f in recentes + sorteados]


def main():
    if not os.path.isdir(PASTA_LAUDOS):
        print(f"[ERRO] pasta {PASTA_LAUDOS} não encontrada - rode o banco_a_downloader.py antes.")
        return 1

    qtd = int(os.getenv("TESTE_QTD") or QTD_PADRAO)
    semente = int(os.getenv("TESTE_SEMENTE") or random.randrange(1, 10**6))
    pdfs = escolher_amostra(qtd, semente)
    if not pdfs:
        print("[ERRO] nenhum PDF em data/laudos.")
        return 1

    print(f"Testando {len(pdfs)} laudos (semente {semente}) - não grava no banco nem em data/imagens...")
    pasta_imagens = tempfile.mkdtemp(prefix="teste_rapido_imagens_")
    inicio = time.time()
    resultados = []
    try:
        with ProcessPoolExecutor(max_workers=min(multiprocessing.cpu_count(), 8)) as ex:
            futuros = [ex.submit(analisar_pdf, p, pasta_imagens) for p in pdfs]
            for i, futuro in enumerate(as_completed(futuros), 1):
                resultados.append(futuro.result())
                if i % 20 == 0 or i == len(futuros):
                    print(f"  ... {i}/{len(futuros)}", flush=True)
    finally:
        shutil.rmtree(pasta_imagens, ignore_errors=True)
    duracao = time.time() - inicio

    total = len(resultados)
    modelos = Counter(r[1] for r in resultados)
    ocorrencias = defaultdict(list)  # checagem -> [(pdf, detalhe)]
    laudos_por_checagem = defaultdict(set)
    for nome, _modelo, problemas in resultados:
        for checagem, detalhe in problemas:
            ocorrencias[checagem].append((nome, detalhe))
            laudos_por_checagem[checagem].add(nome)

    reprovadas = []
    linhas = [
        f"TESTE RÁPIDO DA EXTRAÇÃO (BANCO A) - {datetime.now():%d/%m/%Y %H:%M}",
        f"{total} laudos (semente {semente}): " + ", ".join(f"{n} {m}" for m, n in modelos.most_common()),
        f"Tempo: {duracao:.0f}s ({duracao / max(total, 1):.1f}s por laudo, contando imagens)",
        "",
        "=" * 78,
        "CHECAGENS (laudos afetados / tolerado)",
        "=" * 78,
    ]
    for checagem, limite in LIMITES.items():
        afetados = len(laudos_por_checagem.get(checagem, ()))
        tolerado = int(limite * total)
        estourou = afetados > tolerado
        if estourou:
            reprovadas.append(checagem)
        marca = "PROBLEMA" if estourou else ("ok" if afetados == 0 else "ok (dentro do normal)")
        linhas.append(f"  [{marca:^21}] {checagem}: {afetados} / {tolerado}")

    linhas += ["", "=" * 78, "DETALHES", "=" * 78]
    for checagem in LIMITES:
        if checagem not in ocorrencias:
            continue
        linhas.append(f"  {checagem}:")
        for nome, detalhe in ocorrencias[checagem][:MAX_EXEMPLOS_POR_CHECAGEM]:
            linhas.append(f"    {nome}  {detalhe}")
        if len(ocorrencias[checagem]) > MAX_EXEMPLOS_POR_CHECAGEM:
            linhas.append(f"    ... e mais {len(ocorrencias[checagem]) - MAX_EXEMPLOS_POR_CHECAGEM}")

    linhas += ["", "=" * 78]
    if reprovadas:
        linhas += [
            "VEREDITO: NÃO RODE NA BASE INTEIRA AINDA.",
            "Problema em: " + "; ".join(reprovadas) + ".",
            "Mande este relatório (e 1 ou 2 dos PDFs citados em DETALHES) antes.",
        ]
    else:
        linhas += ["VEREDITO: OK - pode rodar na base inteira."]
    linhas.append("=" * 78)

    relatorio = "\n".join(linhas) + "\n"
    os.makedirs(LOG_DIR, exist_ok=True)
    caminho = os.path.join(LOG_DIR, f"teste_rapido_{datetime.now():%Y%m%d_%H%M%S}.txt")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(relatorio)
    print()
    print(relatorio)
    print(f"Relatório salvo em: {os.path.abspath(caminho)}")
    return 1 if reprovadas else 0


if __name__ == "__main__":
    sys.exit(main())
