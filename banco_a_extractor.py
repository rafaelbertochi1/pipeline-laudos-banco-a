"""
Lê os PDFs de data/laudos/ (laudos do Banco A baixados pelo banco_a_downloader.py)
e grava os dados na tabela laudos_banco_a do Postgres. Chama o banco_a_imagens.py
a cada PDF novo, então já sai com as fotos extraídas também.

Uso: python banco_a_extractor.py

Só lê PDF que ainda não está no banco (pelo nome do arquivo, coluna
`path`). Pra reler tudo, ex.: depois de mudar a leitura:
    $env:FORCAR_REPROCESSAR="1"; python banco_a_extractor.py

Antes de gravar, valida o lote inteiro (checagens.py): se alguma checagem
passar do limite (ex.: laudos sem endereço - sinal de layout novo), não
grava nada, mostra o relatório e sai com código 2 (o rodar_pipeline.py
para aí). Pra gravar mesmo assim: $env:VALIDACAO_IGNORAR="1".

A leitura dos campos (funções extrair_*) é a mesma validada nos laudos
reais do Banco A (Físico/Casa e Eletrônico/Apartamento). O que veio do
banco_b_extractor.py do pipeline-laudos-banco-b é só a volta dela: log em
arquivo, pular o que já foi gravado, validação do lote, reconexão antes
de gravar, ANALYZE no fim e o PC sem suspender enquanto roda.
"""

import os
import re
import sys
import multiprocessing
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal, InvalidOperation
import pdfplumber
import psycopg2
from psycopg2.extras import execute_values

from banco_a_imagens import extrair_imagens_do_laudo, marcar_verificados
import checagens

ZERO = Decimal('0.00')
PASTA_SCRIPT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PASTA_SCRIPT, "logs")


class Tee:
    """Escreve simultaneamente no terminal e num arquivo de log."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, dado):
        for s in self.streams:
            s.write(dado)

    def flush(self):
        for s in self.streams:
            s.flush()


_MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}


def converter_float_seguro(val):
    # Decimal em vez de float pra não gerar sobra de binário
    # (8666.299999999999 em vez de 8666.30) nas colunas NUMERIC.
    if val is None:
        return ZERO
    if isinstance(val, Decimal):
        return val.quantize(Decimal('0.01'))
    if isinstance(val, (int, float)):
        try:
            return Decimal(str(val)).quantize(Decimal('0.01'))
        except InvalidOperation:
            return ZERO

    s_val = str(val).strip()
    if not s_val or s_val.upper() in ['-', '--', '—', 'NONE', 'NULL']:
        return ZERO

    match = re.search(r'[-+]?[\d\.,]+', s_val)
    if not match:
        return ZERO

    num_str = match.group(0)

    if '.' in num_str and ',' in num_str:
        num_str = num_str.replace('.', '').replace(',', '.')
    elif '.' in num_str and ',' not in num_str:
        partes = num_str.split('.')
        if len(partes) == 2 and len(partes[1]) == 3:
            num_str = num_str.replace('.', '')
        elif len(partes) > 2:
            num_str = num_str.replace('.', '')
    elif ',' in num_str:
        num_str = num_str.replace(',', '.')

    try:
        return Decimal(num_str).quantize(Decimal('0.01'))
    except InvalidOperation:
        return ZERO


def converter_int_seguro(val):
    try:
        resultado = int(converter_float_seguro(val))
    except Exception:
        return 0
    # nenhum desses campos (quartos, banheiros, vagas, suítes, idade) é
    # plausivelmente >= 1000 - evita "integer out of range" no banco
    # quando o regex pega um CPF, CEP ou outro número por engano
    return resultado if 0 <= resultado < 1000 else 0


def converter_float_coordenada(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).strip())
    except Exception:
        return None


def limpar_txt(val, valor_padrao=""):
    if not val:
        return valor_padrao
    txt = str(val).strip()
    return txt if txt and txt.upper() != "NULL" else valor_padrao


def _linha_e_indice_coluna(text, label_regex):
    """O relatório da Plataforma pro Banco A é uma tabela de duas colunas onde
    o pdfplumber devolve 'NN - Rótulo A   MM - Rótulo B\\nvalor A   valor B'
    - cada linha de cabeçalho tem 1 ou 2 campos, e a linha seguinte tem os
    valores dos dois na mesma ordem. A numeração (NN/MM) muda entre o
    Laudo Físico e o Eletrônico (e entre Casa/Apartamento), então em vez
    de fixar os números como no extractor do Banco B, acha o rótulo
    pelo texto e conta quantos rótulos "NN - " vêm antes dele na mesma
    linha de cabeçalho pra saber se o valor dele é o 1º ou o 2º token da
    linha de valores seguinte.
    """
    m = re.search(label_regex, text, re.IGNORECASE)
    if not m:
        return None, None
    inicio_linha = text.rfind('\n', 0, m.start()) + 1
    fim_cabecalho = text.find('\n', m.end())
    if fim_cabecalho == -1:
        return None, None
    texto_antes_na_linha = text[inicio_linha:m.start()]
    # -1 porque o próprio rótulo também tem um número na frente ("NN - ",
    # já contado aqui) - só sobra 1 a mais no texto_antes_na_linha por
    # cada rótulo IRMÃO que vier antes dele na mesma linha.
    indice_coluna = max(0, len(re.findall(r'\d+\s*-\s*', texto_antes_na_linha)) - 1)
    fim_valores = text.find('\n', fim_cabecalho + 1)
    if fim_valores == -1:
        fim_valores = len(text)
    linha_valores = text[fim_cabecalho + 1:fim_valores]
    return linha_valores.split(), indice_coluna


def _campo_numerico(text, label_regex, tipo='decimal'):
    tokens, indice = _linha_e_indice_coluna(text, label_regex)
    if not tokens or indice is None or indice >= len(tokens):
        return None
    token = tokens[indice]
    pat = r'\d+,\d{1,2}' if tipo == 'decimal' else r'\d+'
    m = re.search(pat, token)
    return m.group(0) if m else None


def _campo_texto(text, label_regex):
    tokens, indice = _linha_e_indice_coluna(text, label_regex)
    if not tokens or indice is None or indice >= len(tokens):
        return None
    return tokens[indice]


def _area_averbada(text):
    """'Área Averbada' fica ao lado de 'Divisão Interna' no modelo
    Eletrônico - uma lista de cômodos de tamanho variável (Cozinha,
    Dormitório, Suíte...). Quando o 1º item dessa lista tem mais de uma
    palavra (ex: 'Sala de Estar'), a conta de coluna por token de
    `_linha_e_indice_coluna` erra o valor. Como nome de cômodo nunca tem
    número, procura direto o próximo número decimal (com vírgula) depois
    do rótulo, numa janela curta - funciona nos dois modelos.
    """
    m = re.search(
        r'[ÁA]rea\s+Averbada\s*\(em\s*m[²2]\)(.{0,200}?)(\d+,\d{1,2})',
        text, re.IGNORECASE | re.DOTALL
    )
    return m.group(2) if m else None


def extrair_codigo_laudo(text):
    m = re.search(r'#([A-Z]{2,6}\d+)', text)
    return m.group(1) if m else None


def extrair_numero_proposta_e_tipo_laudo(text):
    m = re.search(
        r'Solicitante\s+N[°ºo]\s*da\s*Proposta\s+Tipo\s+de\s+Laudo\n\S+\s+(\d+)\s+(Laudo\s+\S+)',
        text, re.IGNORECASE
    )
    if m:
        return m.group(1), m.group(2).strip()
    return "", ""


def extrair_endereco_numero_complemento(text):
    m = re.search(
        r'Endere[çc]o\s+N[uú]mero\s+Complemento\n(.+?)\s+(\d+|S/N)\s*(.*)',
        text, re.IGNORECASE
    )
    if not m:
        return "", "S/N", ""

    complemento_bruto = m.group(3)
    # quando o Complemento vem vazio no laudo, o pdfplumber às vezes gruda
    # o cabeçalho da linha seguinte ("CEP Bairro Município UF") direto
    # depois do número, na mesma linha - corta o texto ali pra não gravar
    # isso como se fosse um complemento de verdade.
    complemento_bruto = re.split(r'\bCEP\s+Bairro\b', complemento_bruto, flags=re.IGNORECASE)[0]

    return limpar_txt(m.group(1)), limpar_txt(m.group(2), "S/N"), limpar_txt(complemento_bruto)


def _campo_texto_varias_palavras(text, label_regex):
    """Como _campo_texto, mas pra valor que pode ter mais de uma palavra,
    numa linha de dois campos em que o OUTRO valor é sempre uma palavra
    só. 'Tipo do Imóvel' fica ao lado de 'Uso do Imóvel' (Residencial/
    Comercial/Misto) e o tipo pode ser 'Sala Comercial', 'Prédio
    Comercial', 'Imóvel Misto', 'Demais Imóveis Comerciais' - por token,
    gravava só 'Sala', 'Prédio', 'Imóvel', 'Demais'. 'Estado de
    Conservação' fica depois de 'Idade Aparente' (um número) e pode ser
    'Em Construção' - gravava 'Em'. Levantado nos 3.276 laudos de
    jul-set/2026."""
    tokens, indice = _linha_e_indice_coluna(text, label_regex)
    if not tokens or indice is None or indice >= len(tokens):
        return None
    if len(tokens) < 2:
        return tokens[0]
    return " ".join(tokens[:-1]) if indice == 0 else " ".join(tokens[indice:])


# hemisfério depois de cada parte da coordenada em graus/minutos/segundos
# ("20°28'58.72"S 47°25'46.23"W", "9° 9' 9" Sul e 9° 9' 9" Oeste"). Só
# sul/norte/oeste: o Brasil não tem leste, e o "e" minúsculo que liga as
# duas partes não pode virar hemisfério.
_HEMISFERIO = r"(Norte|Sul|Oeste|[NSOW])(?![A-Za-z])"
# entre o número e o hemisfério pode vir qualquer símbolo (°, º, ', `, ",
# e as aspas curvas ’ ” que o Word põe sozinho)
_RE_TEM_HEMISFERIO = re.compile(r"\d[^0-9A-Za-z]{0,4}" + _HEMISFERIO, re.IGNORECASE)
_RE_PARTE_COM_HEMISFERIO = re.compile(r"(.*?)(?<![A-Za-z])" + _HEMISFERIO, re.IGNORECASE)


def _coordenada_de_texto(linha):
    """(lat, lon) a partir do valor digitado à mão pelo vistoriador, ou
    (None, None). Formatos vistos em 3.276 laudos reais: decimal com
    ponto ('-23.49, -46.88', '-23.49,-46.88', '(-23.49, -46.88)',
    '-23.49 -46.88', '-23.49;-46.88'), decimal com vírgula ('-11,42,
    -61,46'), graus/minutos/segundos com hemisfério (°, º, ', `, ", ``)
    e decimal com hemisfério ('22,9° S, 43,1° O'). O resto (texto solto,
    'norte', nome do prédio) fica sem coordenada."""
    if _RE_TEM_HEMISFERIO.search(linha):
        partes = []
        for m in _RE_PARTE_COM_HEMISFERIO.finditer(linha):
            numeros = re.findall(r"\d+(?:[.,]\d+)?", m.group(1))
            if not numeros:
                continue
            # graus, minutos e segundos - os que vierem
            valor = sum(float(n.replace(",", ".")) / d for n, d in zip(numeros[-3:], (1, 60, 3600)))
            if m.group(2).lower() in ("s", "sul", "o", "w", "oeste"):
                valor = -valor
            partes.append(round(valor, 7))
            if len(partes) == 2:
                return partes[0], partes[1]
        return None, None
    numeros = re.findall(r"-?\d{1,3}[.,]\d+", linha)
    if len(numeros) < 2:
        return None, None
    return (converter_float_coordenada(numeros[0].replace(",", ".")),
            converter_float_coordenada(numeros[1].replace(",", ".")))


def extrair_coordenadas(text):
    m = re.search(r'Coordenadas\s+do\s+Im[óo]vel[^\n]*\n([^\n]*)', text, re.IGNORECASE)
    if not m:
        return None, None, None
    lat, lon = _coordenada_de_texto(m.group(1))
    if lat is None or lon is None:
        return None, None, None
    # vistoriador às vezes esquece o "-" ("22.99, -43.44" é no Rio, não no
    # Saara): o Brasil não tem longitude positiva nem latitude acima de 6.
    # O que ainda cair fora do Brasil é digitação sem conserto ("410093S
    # 382802w", "18°2655 S") - melhor sem coordenada que uma errada.
    if 6 < lat <= 34:
        lat = -lat
    if 28 <= lon <= 75:
        lon = -lon
    if not (-34 <= lat <= 6 and -75 <= lon <= -28):
        return None, None, None
    return f"{lat}, {lon}", lat, lon


def extrair_data_vistoria(text):
    m = re.search(r'Data\s+da\s+Vistoria[^\n]*\n([^\n]+)', text, re.IGNORECASE)
    if m:
        data_m = re.search(r'(\d{2}/\d{2}/\d{4})', m.group(1))
        if data_m:
            return data_m.group(1)

    # Laudo Eletrônico não tem seção "DADOS DA VISTORIA" - usa a data de
    # assinatura do laudo como aproximação ("...-feira, 1 de Setembro de 2026").
    m = re.search(
        r'-feira,\s*(\d{1,2})\s+de\s+([A-Za-zÀ-ÿ]+)\s+de\s+(\d{4})',
        text, re.IGNORECASE
    )
    if m:
        dia, mes_nome, ano = m.groups()
        mes = _MESES_PT.get(mes_nome.lower())
        if mes:
            return f"{int(dia):02d}/{mes:02d}/{ano}"
    return None


def extrair_valor_avaliacao(text):
    m = re.search(
        r'VALOR\s+DA\s+AVALIA[ÇC][ÃA]O\s*\n\s*R?\$?\s*([\d\.,]+)',
        text, re.IGNORECASE
    )
    return m.group(1) if m else None


def extrair_area_terreno(text):
    """A seção TERRENO do laudo só traz uma área própria quando o imóvel é
    'Isolado' (terreno exclusivo) - quando é 'Condomínio', só mostra
    Fração Ideal (%), sem m² (nesse caso retorna None, o que é esperado,
    não é falha de extração). Procura só DEPOIS do cabeçalho 'TERRENO'
    pra não confundir com a área do imóvel (construção) que vem antes.
    """
    m_secao = re.search(r'\bTERRENO\b', text)
    if not m_secao:
        return None
    texto_apos_terreno = text[m_secao.end():]
    valor = _campo_numerico(texto_apos_terreno, r'[ÁA]rea\s+[Tt]otal\s*\(em\s*m[²2]\)')
    if valor is None:
        valor = _campo_numerico(texto_apos_terreno, r'[ÁA]rea\s+Averbada\s*\(em\s*m[²2]\)')
    return valor


def extrair_dados_pdf(pdf_path):
    # Roda dentro de um worker separado (ProcessPoolExecutor) - devolve o
    # resultado pro processo principal ({"status": "ok"/"vazio"/"erro"})
    # em vez de imprimir aqui, porque um print() dentro do worker não
    # passa pelo log em arquivo do processo principal.
    file_name = os.path.basename(pdf_path)
    try:
        # salva a foto da fachada e uma de cada cômodo junto (data/imagens/)
        # - num try próprio pra que problema em imagem nunca derrube a
        # extração dos dados.
        imagens_verificadas = False
        try:
            imagens_verificadas = extrair_imagens_do_laudo(pdf_path)
        except Exception as e:
            print(f"[ERRO IMAGEM] {file_name}: {str(e)}")

        with pdfplumber.open(pdf_path) as pdf:
            full_text = ""
            for page in pdf.pages:
                full_text += (page.extract_text() or "") + "\n"

            if not full_text.strip():
                return {"status": "vazio", "path": file_name}

            # "LAUDO DE INSPEÇÃO" é só a vistoria (sem valor, sem proposta) -
            # a Plataforma entrega no lugar da avaliação quando ela ainda não
            # saiu. Não grava: com o path no banco, a avaliação de verdade
            # (mesmo nome de arquivo) nunca seria extraída depois.
            if re.search(r'LAUDO\s+DE\s+INSPE', full_text[:300], re.IGNORECASE):
                return {"status": "inspecao", "path": file_name}

            numero_proposta, tipo_laudo = extrair_numero_proposta_e_tipo_laudo(full_text)
            modelo_usado = "eletronico" if "eletr" in tipo_laudo.lower() else "fisico"

            endereco, numero, complemento = extrair_endereco_numero_complemento(full_text)
            coords_str, lat, lon = extrair_coordenadas(full_text)

            tipo_imovel = _campo_texto_varias_palavras(full_text, r'Tipo\s+do\s+Im[óo]vel')
            padrao_acabamento = _campo_texto(full_text, r'Padr[ãa]o\s+de\s+Acabamento\s+do\s+Im[óo]vel')
            estado_conservacao = _campo_texto_varias_palavras(full_text,r'Estado\s+de\s+Conserva[çc][ãa]o\s+do\s+Im[óo]vel')

            area_averbada = _area_averbada(full_text)
            area_comum = _campo_numerico(full_text, r'[ÁA]rea\s+Comum\s*\(em\s*m[²2]\)')
            area_total = _campo_numerico(full_text, r'[ÁA]rea\s+[Tt]otal\s*\(em\s*m[²2]\)')
            area_nao_averbada = _campo_numerico(full_text, r'[ÁA]rea\s+n[ãa]o\s+Averbada\s*\(em\s*m[²2]\)')
            area_terreno = extrair_area_terreno(full_text)

            quartos = _campo_numerico(full_text, r'Total\s+de\s+Dormit[óo]rios', tipo='int')
            suites = _campo_numerico(full_text, r'N[°º]\s*de\s*Su[íi]tes', tipo='int')
            banheiros = _campo_numerico(full_text, r'Total\s+de\s+Banheiros', tipo='int')
            vagas = _campo_numerico(full_text, r'Total\s+de\s+Vagas', tipo='int')
            idade_anos = _campo_numerico(full_text, r'Idade\s+Aparente', tipo='int')

            data_avaliacao = extrair_data_vistoria(full_text)
            valor_avaliacao = extrair_valor_avaliacao(full_text)

            area_privativa_dec = converter_float_seguro(area_averbada)
            area_terreno_dec = converter_float_seguro(area_terreno)
            valor_mercado_dec = converter_float_seguro(valor_avaliacao)

            # valor unitário não vem mais direto do PDF (o laudo às vezes usa
            # uma terceira métrica, "Área Estimada", pra essa conta, o que
            # confundia comparação com area_privativa_m2 gravado aqui) -
            # agora é sempre calculado por nós: valor de mercado dividido
            # pela área privativa, ou pela área de terreno quando não há
            # área privativa (caso de terreno).
            if area_privativa_dec > 0:
                valor_unitario_m2 = (valor_mercado_dec / area_privativa_dec).quantize(Decimal('0.01'))
            elif area_terreno_dec > 0:
                valor_unitario_m2 = (valor_mercado_dec / area_terreno_dec).quantize(Decimal('0.01'))
            else:
                valor_unitario_m2 = ZERO

            dados = {
                "numero_proposta": numero_proposta,
                "codigo_laudo": extrair_codigo_laudo(full_text),
                "data_avaliacao": data_avaliacao,
                "endereco": endereco,
                "numero": numero,
                "complemento": complemento,
                "tipo_imovel": limpar_txt(tipo_imovel, "Apartamento"),
                "area_privativa_m2": area_privativa_dec,
                "area_comum_m2": converter_float_seguro(area_comum),
                "area_total_m2": converter_float_seguro(area_total),
                "area_nao_averbada_m2": converter_float_seguro(area_nao_averbada),
                "area_terreno_m2": area_terreno_dec,
                "quartos": converter_int_seguro(quartos),
                "suites": converter_int_seguro(suites),
                "banheiros": converter_int_seguro(banheiros),
                "vagas": converter_int_seguro(vagas),
                "idade_anos": converter_int_seguro(idade_anos),
                "padrao_acabamento": limpar_txt(padrao_acabamento, "Normal"),
                "estado_conservacao": limpar_txt(estado_conservacao, "Bom"),
                "valor_mercado": valor_mercado_dec,
                # o modelo do Banco A não tem um valor de "venda forçada"
                # separado (diferente do Banco B) - fica zerado.
                "valor_venda_forcada": ZERO,
                "valor_unitario_m2": valor_unitario_m2,
                "coordenadas": coords_str,
                "latitude": lat,
                "longitude": lon,
                "path": file_name,
                "modelo_usado": modelo_usado,
            }
            # campos que o laudo não trouxe (ou que a leitura não achou) e
            # foram gravados com o valor padrão de limpar_txt - vai fora de
            # `dados` (não é coluna) só pra validação do lote contar: se um
            # layout antigo mudar o rótulo "Tipo do Imóvel", todo laudo
            # viraria "Apartamento" sem ninguém perceber.
            nao_lidos = [campo for campo, bruto in (("tipo_imovel", tipo_imovel),
                                                    ("padrao_acabamento", padrao_acabamento),
                                                    ("estado_conservacao", estado_conservacao))
                         if not limpar_txt(bruto)]
            return {"status": "ok", "dados": dados, "nao_lidos": nao_lidos,
                    "imagens_verificadas": imagens_verificadas}
    except Exception as e:
        return {"status": "erro", "path": file_name, "mensagem": str(e)}


def processar_em_lote():
    folder_path = os.path.join(PASTA_SCRIPT, "data", "laudos")
    if not os.path.exists(folder_path):
        folder_path = PASTA_SCRIPT

    todos_pdfs = [f for f in os.listdir(folder_path) if f.endswith('.pdf')]
    print(f"Total de PDFs encontrados: {len(todos_pdfs)}")

    if not todos_pdfs:
        return

    host_pg = os.getenv("PGURL", "127.0.0.1")
    dbname = os.getenv("PGNAME", "testdb")
    user = os.getenv("PGUSR", "postgres")
    password = os.getenv("PGPASS", "postgres")
    port = os.getenv("PGPORT", "5432")

    def conectar():
        return psycopg2.connect(host=host_pg, dbname=dbname, user=user, password=password, port=port)

    try:
        conn = conectar()
    except Exception as e:
        print(f"[ERRO CARGA BANCO]: {str(e)}")
        return

    with conn:
        with conn.cursor() as cursor:
            # mesmas colunas da tabela 'laudos' (Banco B) de quando esta
            # tabela foi criada - só muda o nome, pra manter os dois bancos
            # com o mesmo formato e permitir consultas/relatórios
            # unificados depois. (A `laudos` do Banco B ganhou colunas
            # depois - bairro, município, UF, CEP, infraestrutura... - que
            # aqui só entram quando forem pedidas.)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS laudos_banco_a (
                    id SERIAL PRIMARY KEY,
                    numero_proposta TEXT,
                    codigo_laudo TEXT,
                    data_avaliacao TEXT,
                    endereco TEXT,
                    numero TEXT,
                    complemento TEXT,
                    tipo_imovel TEXT,
                    area_privativa_m2 NUMERIC,
                    area_comum_m2 NUMERIC,
                    area_total_m2 NUMERIC,
                    area_nao_averbada_m2 NUMERIC,
                    area_terreno_m2 NUMERIC,
                    quartos INTEGER,
                    suites INTEGER,
                    banheiros INTEGER,
                    vagas INTEGER,
                    idade_anos INTEGER,
                    padrao_acabamento TEXT,
                    estado_conservacao TEXT,
                    valor_mercado NUMERIC,
                    valor_venda_forcada NUMERIC,
                    valor_unitario_m2 NUMERIC,
                    coordenadas TEXT,
                    latitude DOUBLE PRECISION,
                    longitude DOUBLE PRECISION,
                    path TEXT,
                    modelo_usado TEXT
                );
            """)
            # tabela pode já existir de uma execução anterior sem essas
            # colunas - adiciona se faltar, sem apagar os dados já gravados.
            cursor.execute("ALTER TABLE laudos_banco_a ADD COLUMN IF NOT EXISTS area_nao_averbada_m2 NUMERIC;")
            cursor.execute("ALTER TABLE laudos_banco_a ADD COLUMN IF NOT EXISTS area_terreno_m2 NUMERIC;")
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS laudos_banco_a_codigo_laudo_key
                ON laudos_banco_a (codigo_laudo) WHERE codigo_laudo IS NOT NULL;
            """)

            # pula PDF que já está no banco - com dezenas de milhares de
            # laudos históricos, reler tudo a cada execução custava horas.
            # FORCAR_REPROCESSAR=1 ignora esse filtro (ex: depois de mudar
            # a lógica de extração e precisar atualizar laudos antigos -
            # o upsert é por codigo_laudo, então isso atualiza em vez de
            # duplicar, sem precisar apagar nada no banco antes).
            forcar_reprocessar = os.getenv("FORCAR_REPROCESSAR", "0") == "1"
            if forcar_reprocessar:
                ja_processados = set()
                print("[INFO] FORCAR_REPROCESSAR=1 - reprocessando todos os PDFs da pasta, mesmo os já gravados.")
            else:
                cursor.execute("SELECT path FROM laudos_banco_a WHERE path IS NOT NULL;")
                ja_processados = {row[0] for row in cursor.fetchall()}

    # a extração de milhares de PDFs leva mais de uma hora; uma conexão
    # aberta parada esse tempo todo cai ("connection already closed") e a
    # carga inteira se perde. Fecha aqui e abre outra na hora de gravar.
    conn.close()

    pdf_files = [os.path.join(folder_path, f) for f in todos_pdfs if f not in ja_processados]
    print(f"  {len(todos_pdfs) - len(pdf_files)} já estavam no banco (extração pulada), "
          f"{len(pdf_files)} novo(s) pra processar.")

    if not pdf_files:
        print("Nada novo pra extrair.")
        return

    num_workers = min(multiprocessing.cpu_count(), 8)
    dados_extraidos = []
    nao_lidos_por_path = {}
    vazios = []
    so_inspecao = []
    erros_parser = []
    imagens_verificadas = []

    print(f"Iniciando extração paralela em {num_workers} workers...")
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(extrair_dados_pdf, f): f for f in pdf_files}
        for concluidos, future in enumerate(as_completed(futures), start=1):
            # com milhares de PDFs a extração leva bem mais de uma hora -
            # sem isso o terminal fica mudo e parece que travou
            if concluidos % 500 == 0 or concluidos == len(futures):
                print(f"  ... {concluidos}/{len(futures)} PDFs lidos", flush=True)
            resultado = future.result()
            if resultado.get("imagens_verificadas"):
                imagens_verificadas.append(futures[future])
            if resultado["status"] == "ok":
                dados_extraidos.append(resultado["dados"])
                nao_lidos_por_path[resultado["dados"]["path"]] = resultado.get("nao_lidos", [])
            elif resultado["status"] == "vazio":
                vazios.append(resultado["path"])
            elif resultado["status"] == "inspecao":
                so_inspecao.append(resultado["path"])
            else:
                erros_parser.append((resultado["path"], resultado["mensagem"]))

    # PDFs com as imagens tiradas agora do zero entram na lista de
    # verificados da etapa de imagens - senão ela reabre todos de novo
    marcar_verificados(imagens_verificadas)

    print(f"Extração concluída. Total laudos: {len(dados_extraidos)}")
    if vazios:
        print(f"[AVISO] {len(vazios)} PDF(s) sem texto extraível (provavelmente escaneado como imagem):")
        for p in vazios:
            print(f"  {p}")
    if so_inspecao:
        print(f"[AVISO] {len(so_inspecao)} PDF(s) são Laudo de Inspeção (só a vistoria, sem valor) - "
              "não gravados. Tire-os de data/laudos/ pro robô baixar a avaliação quando sair:")
        for p in sorted(so_inspecao):
            print(f"  {p}")
    if erros_parser:
        print(f"[AVISO] {len(erros_parser)} PDF(s) com erro na extração:")
        for p, msg in erros_parser:
            print(f"  {p}: {msg}")
    if not dados_extraidos:
        return

    if not validar_lote(dados_extraidos, erros_parser, nao_lidos_por_path):
        # código 2: o rodar_pipeline.py para aqui, sem a etapa de imagens
        sys.exit(2)

    colunas = list(dados_extraidos[0].keys())

    # Avisa se dois ou mais arquivos extraíram o MESMO código de laudo -
    # isso não deveria acontecer de verdade (cada inspeção tem um código
    # único), então é sinal de erro de extração nesses arquivos. Repetir
    # N° de Proposta é normal (uma proposta pode ter mais de um laudo).
    arquivos_por_codigo = defaultdict(list)
    for d in dados_extraidos:
        if d["codigo_laudo"]:
            arquivos_por_codigo[d["codigo_laudo"]].append(d["path"])
    duplicados = {k: v for k, v in arquivos_por_codigo.items() if len(v) > 1}
    if duplicados:
        print("\n[AVISO] Mais de um arquivo extraiu o mesmo código de laudo (isso não deveria acontecer):")
        for codigo, arquivos in duplicados.items():
            print(f"  Código {codigo}: {', '.join(arquivos)}")
        print("  (o último desses arquivos processado vai prevalecer no banco -")
        print("  vale conferir manualmente a extração desses PDFs)\n")

    set_clause = ",\n            ".join(
        f"{col} = EXCLUDED.{col}" for col in colunas if col != "codigo_laudo"
    )
    placeholders = ", ".join(["%s"] * len(colunas))
    query_upsert_lote = f"""
        INSERT INTO laudos_banco_a ({', '.join(colunas)})
        VALUES %s
        ON CONFLICT (codigo_laudo) WHERE codigo_laudo IS NOT NULL DO UPDATE SET
            {set_clause};
    """
    query_upsert_linha = f"""
        INSERT INTO laudos_banco_a ({', '.join(colunas)})
        VALUES ({placeholders})
        ON CONFLICT (codigo_laudo) WHERE codigo_laudo IS NOT NULL DO UPDATE SET
            {set_clause};
    """

    # um único INSERT não pode atualizar a mesma linha duas vezes, então
    # dedup por codigo_laudo antes (mantém o último) pra poder gravar tudo
    # num lote só
    por_codigo = {}
    sem_codigo = []
    for d in dados_extraidos:
        if d["codigo_laudo"]:
            por_codigo[d["codigo_laudo"]] = d
        else:
            sem_codigo.append(d)
    dados_para_gravar = list(por_codigo.values()) + sem_codigo

    try:
        conn = conectar()
    except Exception as e:
        print(f"[ERRO CARGA BANCO] não conseguiu reconectar pra gravar: {str(e)}")
        return

    try:
        with conn:
            with conn.cursor() as cursor:
                valores = [[dados[col] for col in colunas] for dados in dados_para_gravar]
                try:
                    execute_values(cursor, query_upsert_lote, valores, page_size=500)
                    print(f"[SUCESSO] {len(valores)} laudo(s) gravado(s) no banco (carga em lote). 0 com erro.")
                except Exception as e:
                    # se o lote falhar, cai pro linha-a-linha pra isolar o arquivo com problema
                    conn.rollback()
                    print(f"[AVISO] Carga em lote falhou ({str(e).splitlines()[0]}) - tentando linha por linha...")
                    gravados = 0
                    falhas = 0
                    for dados in dados_para_gravar:
                        linha_valores = [dados[col] for col in colunas]
                        try:
                            cursor.execute(query_upsert_linha, linha_valores)
                            gravados += 1
                        except Exception as e2:
                            conn.rollback()
                            falhas += 1
                            print(f"[ERRO - PULADO] {dados.get('path')}: {str(e2).splitlines()[0]}")
                    print(f"[SUCESSO] {gravados} laudo(s) gravado(s) no banco. {falhas} com erro (pulados).")
        atualizar_estatisticas(conn)
    except Exception as e:
        print(f"[ERRO CARGA BANCO]: {str(e)}")
    finally:
        conn.close()


def validar_lote(dados_extraidos, erros_parser=(), nao_lidos_por_path=None):
    """Checa o lote inteiro antes de gravar (checagens.py, as mesmas do
    testar_amostra.py) - já leu os PDFs, então sai de graça, em vez de
    uma leitura a mais só pra validar. Se alguma checagem passar do
    limite, nada é gravado (no Banco B foi assim que um lote inteiro de
    laudos sem endereço deixou de entrar). VALIDACAO_IGNORAR=1 grava
    mesmo assim."""
    laudos_por_checagem = defaultdict(set)
    exemplos = defaultdict(list)
    nao_lidos_por_path = nao_lidos_por_path or {}
    for d in dados_extraidos:
        for checagem, detalhe in checagens.problemas_dos_dados(d, nao_lidos_por_path.get(d.get("path"), ())):
            laudos_por_checagem[checagem].add(d.get("path"))
            if len(exemplos[checagem]) < 3:
                exemplos[checagem].append(f"{d.get('path')} {detalhe}".strip())
    contagem = {c: len(p) for c, p in laudos_por_checagem.items()}
    if erros_parser:
        contagem["erro ao processar o PDF"] = len(erros_parser)
        exemplos["erro ao processar o PDF"] = [f"{p} {m}" for p, m in list(erros_parser)[:3]]
    total = len(dados_extraidos) + len(erros_parser)
    estouradas = checagens.checagens_estouradas(contagem, total)

    print(f"Validação do lote ({total} laudos):")
    if not contagem:
        print("  nenhum problema encontrado.")
    for checagem, n in sorted(contagem.items(), key=lambda x: -x[1]):
        marca = "ACIMA" if checagem in estouradas else "ok   "
        limite = checagens.LIMITES_DADOS.get(checagem, 0)
        print(f"  {marca} {checagem}: {n} ({n / total:.1%}, limite {limite:.0%}) - ex.: {exemplos[checagem][:2]}")
    if not estouradas:
        return True
    if os.getenv("VALIDACAO_IGNORAR", "0") == "1":
        print("[AVISO] Checagem acima do limite, mas VALIDACAO_IGNORAR=1 - gravando mesmo assim.")
        return True
    print("[BLOQUEADO] Nada foi gravado no banco: as checagens marcadas ACIMA passaram do limite "
          "(provável layout novo de laudo). Mande este log pra investigar. Pra gravar mesmo assim: "
          '$env:VALIDACAO_IGNORAR="1"')
    return False


def atualizar_estatisticas(conn):
    """ANALYZE depois da carga: a contagem "~ N registros" do Adminer (e o
    plano das consultas) vem da estatística do Postgres, que o autovacuum
    só refaz de vez em quando - depois de uma carga ela ficava com o
    número antigo. Não mexe em dado nenhum; se falhar, só avisa."""
    try:
        with conn:
            with conn.cursor() as cursor:
                cursor.execute("ANALYZE laudos_banco_a;")
        print("[INFO] Estatísticas do banco atualizadas (ANALYZE) - o Adminer já mostra a contagem nova.")
    except Exception as e:
        print(f"[AVISO] ANALYZE falhou ({str(e).splitlines()[0]}) - os dados foram gravados, "
              "só a contagem aproximada do Adminer fica desatualizada.")


def manter_pc_acordado(ligar):
    """Impede o Windows de entrar em suspensão enquanto a extração roda (ela
    leva mais de uma hora com a pasta toda, e a suspensão mata o processo).
    Só segura a suspensão - a tela ainda apaga e bloqueia normalmente, o
    que não atrapalha o script. O Windows libera sozinho se o processo
    morrer; fora do Windows não faz nada."""
    if sys.platform != "win32":
        return
    import ctypes
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(
        ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if ligar else 0))


if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"extracao_{datetime.now():%Y%m%d_%H%M%S}.txt")
    log_file = open(log_path, "w", encoding="utf-8")
    stdout_original = sys.stdout
    sys.stdout = Tee(stdout_original, log_file)
    print(f"Log desta execução: {log_path}")
    manter_pc_acordado(True)
    if sys.platform == "win32":
        print("[INFO] Suspensão do PC bloqueada até a extração terminar.")
    try:
        processar_em_lote()
    finally:
        manter_pc_acordado(False)
        sys.stdout = stdout_original
        log_file.close()
