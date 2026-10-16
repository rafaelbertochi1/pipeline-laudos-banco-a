"""rodar_pipeline.py: as etapas rodam em ordem e o pipeline para na
primeira que falhar (código 2 do extrator = lote bloqueado pela validação)."""
import subprocess
import sys

import pytest

import rodar_pipeline


class Resultado:
    def __init__(self, codigo):
        self.returncode = codigo


def test_etapas_em_ordem_com_o_mesmo_python(monkeypatch):
    chamados = []

    def rodar(comando, cwd):
        chamados.append(comando)
        return Resultado(0)
    monkeypatch.setattr(subprocess, "run", rodar)
    rodar_pipeline.main()
    assert [c[1].replace("\\", "/").rsplit("/", 1)[-1] for c in chamados] == [
        "banco_a_downloader.py", "banco_a_extractor.py", "banco_a_imagens.py",
    ]
    assert all(c[0] == sys.executable for c in chamados)


def test_lote_bloqueado_para_o_pipeline(monkeypatch):
    """Se o extrator sai com 2, a etapa de imagens não roda."""
    chamados = []

    def rodar(comando, cwd):
        chamados.append(comando[1])
        return Resultado(2 if comando[1].endswith("banco_a_extractor.py") else 0)
    monkeypatch.setattr(subprocess, "run", rodar)
    with pytest.raises(SystemExit) as saida:
        rodar_pipeline.main()
    assert saida.value.code == 2
    assert len(chamados) == 2
