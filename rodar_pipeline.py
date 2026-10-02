"""
Roda o pipeline completo do Banco A (download + extração + imagens) numa
execução só e mostra um mini relatório de tempo de cada etapa no final.

Uso: python rodar_pipeline.py

Continua pedindo a data inicial/final no terminal (é o banco_a_downloader.py
rodando por baixo) - só não precisa mais rodar os comandos separados nem
calcular o tempo de cada um na mão. Pra não digitar as datas:
    $env:PERIODO_INICIO="01/01/2023"; $env:PERIODO_FIM="31/12/2023"
Período grande vai em lotes de até DIAS_POR_LOTE dias (padrão 31), um de
cada vez, e a extração só começa depois que o download inteiro termina.

Mesmo rodar_pipeline.py do pipeline-laudos-banco-b (Banco B), sem a etapa de
coordenadas pelo endereço: o laudo do Banco A imprime "Coordenadas do
Imóvel" e a tabela laudos_banco_a não tem município/UF/CEP pra consultar.
"""

import os
import subprocess
import sys
import time

PASTA_SCRIPT = os.path.dirname(os.path.abspath(__file__))


def formatar_duracao(segundos):
    minutos, segundos = divmod(int(segundos), 60)
    horas, minutos = divmod(minutos, 60)
    if horas:
        return f"{horas}h{minutos:02d}min{segundos:02d}s"
    return f"{minutos}min{segundos:02d}s"


def rodar_etapa(titulo, script):
    print("=" * 60)
    print(f" {titulo}")
    print("=" * 60)
    inicio = time.time()
    # usa o mesmo interpretador Python que está rodando este script
    # (respeita venv/ambiente ativo, em vez de assumir "python" no PATH)
    resultado = subprocess.run([sys.executable, os.path.join(PASTA_SCRIPT, script)], cwd=PASTA_SCRIPT)
    duracao = time.time() - inicio

    if resultado.returncode != 0:
        print(f"\n[ERRO] {script} terminou com código {resultado.returncode} "
              f"após {formatar_duracao(duracao)}.")
        sys.exit(resultado.returncode)

    return duracao


def main():
    inicio_total = time.time()

    tempo_downloader = rodar_etapa(
        "1/3 - BAIXANDO LAUDOS (Plataforma - Banco A)", "banco_a_downloader.py"
    )
    tempo_extractor = rodar_etapa(
        "2/3 - EXTRAINDO E GRAVANDO NO BANCO (laudos_banco_a)", "banco_a_extractor.py"
    )
    # o extrator já salva as imagens dos laudos novos; esta etapa passa
    # pela pasta toda (completa o que faltar). Só abre PDF ainda não
    # verificado, então depois da primeira vez é rápida.
    tempo_imagens = rodar_etapa(
        "3/3 - IMAGENS (fachada e cômodos)", "banco_a_imagens.py"
    )

    tempo_total = time.time() - inicio_total

    print("\n" + "#" * 60)
    print("#  MINI RELATÓRIO - PIPELINE COMPLETO (BANCO A)")
    print("#" * 60)
    print(f"  Download (Plataforma):      {formatar_duracao(tempo_downloader)}")
    print(f"  Extração (banco de dados): {formatar_duracao(tempo_extractor)}")
    print(f"  Imagens:                   {formatar_duracao(tempo_imagens)}")
    print(f"  Tempo total:               {formatar_duracao(tempo_total)}")
    print("#" * 60)


if __name__ == "__main__":
    main()
