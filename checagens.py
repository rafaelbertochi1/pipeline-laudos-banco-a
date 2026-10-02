"""Checagens dos dados extraídos de um laudo do Banco A - usadas pelo
testar_amostra.py (numa amostra, antes de rodar na base) e pelo
banco_a_extractor.py (em todo lote, antes de gravar: se alguma passar do
limite, o lote não é gravado).

Mesma ideia do checagens.py do pipeline-laudos-banco-b (Banco B), com só os
campos que a tabela laudos_banco_a tem: sem amostras, sem município/UF, sem
venda forçada (o laudo do Banco A não traz esses conceitos)."""

import re

# Checagem -> fração tolerável do lote. 0 = bug conhecido. Os limites
# acima de zero seguem os do Banco B (~0,1-0,5% de laudo que realmente
# não traz o campo, em 10 mil laudos); ajustar quando a base do Banco A
# mostrar o que é normal nela.
LIMITES_DADOS = {
    "erro ao processar o PDF": 0,
    "sem código de laudo": 0,
    "padrão/estado com pedaço de outra coluna": 0,
    "sem número de proposta": 0.05,
    "sem tipo de imóvel": 0.05,
    "valor de mercado zerado": 0.05,
    "sem área (privativa e terreno zeradas)": 0.05,
    "sem endereço": 0.05,
    "sem data de avaliação": 0.05,
    "quartos/banheiros acima de 15": 0.05,
    # tipo/estado de mais de uma palavra gravado só com a primeira ("Sala"
    # em vez de "Sala Comercial", "Em" em vez de "Em Construção") - bug
    # corrigido em out/2026, não pode voltar
    "tipo/estado com palavra cortada": 0,
    # coordenada digitada à mão em formato que o parser não lê (graus/
    # minutos/segundos, vírgula decimal...) - eram 4,5% em jul-set/2026
    # antes da correção, 0,9% depois (texto solto tipo "norte", "centro")
    "sem coordenadas": 0.03,
    # o parser grava "Apartamento"/"Normal"/"Bom" quando NÃO acha o campo,
    # então `d` sozinho não mostra a falha - o extrator passa à parte quais
    # ficaram no padrão (`nao_lidos`). Laudo que de fato não traz padrão de
    # acabamento existe (terreno), por isso há folga
    "tipo/padrão/estado não lido (gravado com o valor padrão)": 0.05,
}

# no extrator, um lote só é barrado se a checagem passar do limite E
# aparecer em mais que isso de laudos: laudo isolado esquisito (PDF
# quebrado, formato único) não trava a carga inteira - bug de layout
# aparece em dezenas ou centenas
FOLGA_LAUDOS = 3

# primeira palavra de tipo/estado que no laudo do Banco A sempre vem com
# mais palavras depois (levantado em 3.276 laudos de jul-set/2026)
TIPOS_CORTADOS = {"Sala", "Prédio", "Imóvel", "Demais"}
ESTADOS_CORTADOS = {"Em"}


def texto_contaminado(valor):
    """Padrão/estado com número ou comprido demais = pedaço de outra coluna
    do PDF (no Banco A o rótulo e o valor ficam em linhas diferentes, e um
    token a mais na linha de valores desloca tudo)."""
    texto = str(valor or "")
    return bool(re.search(r"\d", texto)) or len(texto) > 40


def problemas_dos_dados(d, nao_lidos=()):
    """[(checagem, detalhe)] dos dados de um laudo já extraído. `nao_lidos`:
    campos que o extrator não achou e gravou com o valor padrão."""
    problemas = []

    def p(checagem, detalhe=""):
        problemas.append((checagem, detalhe))

    if not d.get("codigo_laudo"):
        p("sem código de laudo")
    if not d.get("numero_proposta"):
        p("sem número de proposta")
    if not d.get("tipo_imovel"):
        p("sem tipo de imóvel")
    if not d.get("valor_mercado"):
        p("valor de mercado zerado")
    if not d.get("area_privativa_m2") and not d.get("area_terreno_m2"):
        p("sem área (privativa e terreno zeradas)")
    if not d.get("endereco"):
        p("sem endereço")
    if not d.get("data_avaliacao"):
        p("sem data de avaliação")
    for campo in ("padrao_acabamento", "estado_conservacao"):
        if texto_contaminado(d.get(campo)):
            p("padrão/estado com pedaço de outra coluna", f"{campo}={d.get(campo)!r}")
    if (d.get("quartos") or 0) > 15 or (d.get("banheiros") or 0) > 15:
        p("quartos/banheiros acima de 15", f"{d.get('quartos')} quartos, {d.get('banheiros')} banheiros")
    if d.get("tipo_imovel") in TIPOS_CORTADOS or d.get("estado_conservacao") in ESTADOS_CORTADOS:
        p("tipo/estado com palavra cortada", f"{d.get('tipo_imovel')!r} / {d.get('estado_conservacao')!r}")
    if d.get("latitude") is None or d.get("longitude") is None:
        p("sem coordenadas")
    if nao_lidos:
        p("tipo/padrão/estado não lido (gravado com o valor padrão)", ", ".join(nao_lidos))
    return problemas


def checagens_estouradas(contagem_laudos, total):
    """{checagem: laudos} das que passaram do limite no lote.
    `contagem_laudos`: {checagem: nº de laudos com ela}."""
    return {
        checagem: n for checagem, n in contagem_laudos.items()
        if n > LIMITES_DADOS.get(checagem, 0) * total + FOLGA_LAUDOS
    }
