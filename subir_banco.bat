@echo off
REM Abre o Docker Desktop (se nao estiver aberto), espera ele ficar pronto,
REM e sobe o Postgres + Adminer (postgres_pdf + adminer_pdf) - o mesmo banco
REM usado pelo pipeline-laudos-banco-b (Banco B) e por este projeto (Banco A).
REM
REM Se a pasta do pipeline-laudos-banco-b existir nesta maquina (ao lado desta
REM pasta ou em Documents\GitHub), usa o docker-compose.yml de la, pra os
REM dois projetos compartilharem exatamente o mesmo container e volume.
REM Se nao existir, usa o docker-compose.yml desta pasta, com o MESMO nome
REM de projeto que o Compose daria pra pasta do Banco B - assim, se o
REM pipeline-laudos-banco-b for clonado depois, ele encontra o banco ja criado
REM em vez de subir um segundo.
REM
REM Sem acento neste arquivo de proposito: .bat com acento embaralha
REM dependendo da codificacao do terminal.

setlocal

REM o instalador novo do Docker Desktop pode instalar so pro usuario
REM (AppData\Local\Programs\DockerDesktop) em vez de Program Files -
REM procura nos dois lugares
set "DOCKER_DESKTOP_EXE=C:\Program Files\Docker\Docker\Docker Desktop.exe"
if not exist "%DOCKER_DESKTOP_EXE%" if exist "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe" set "DOCKER_DESKTOP_EXE=%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe"
set "BASE_GITHUB=%USERPROFILE%\Documents\GitHub"
set "PASTA_AQUI=%~dp0"
REM nome que o Docker Compose da ao projeto da pasta
REM "Backend l Script Extracao Laudos" (minusculo, so letras e numeros)
set "PROJETO_COMPOSE=backendlscriptextraolaudos"
set "FALHOU="

echo ============================================================
echo  Subindo o banco (Postgres + Adminer)
echo ============================================================

REM --- 1. Verifica se o Docker ja esta rodando ---
docker info >nul 2>&1
if not errorlevel 1 goto docker_pronto

echo [1/3] Docker Desktop nao esta rodando - abrindo...
if not exist "%DOCKER_DESKTOP_EXE%" (
    echo.
    echo [ERRO] Nao encontrei o Docker Desktop em:
    echo   %DOCKER_DESKTOP_EXE%
    echo Instale o Docker Desktop ^(com WSL 2^) ou edite a linha
    echo DOCKER_DESKTOP_EXE no topo deste .bat com o caminho certo.
    pause
    exit /b 1
)
start "" "%DOCKER_DESKTOP_EXE%"

echo       Aguardando o Docker iniciar (pode levar um tempo na primeira vez)...
set /a TENTATIVAS=0
:esperar_docker
REM ping em vez de "timeout /t 3": o timeout falha na hora (sem esperar)
REM quando o .bat e chamado por outro programa, e as 60 tentativas
REM acabavam em segundos
ping -n 4 127.0.0.1 >nul
set /a TENTATIVAS+=1
docker info >nul 2>&1
if not errorlevel 1 goto docker_pronto
if %TENTATIVAS% geq 60 (
    echo.
    echo [ERRO] O Docker nao ficou pronto em 3 minutos. Abra o Docker Desktop
    echo e veja o que ele esta mostrando ^(WSL faltando? virtualizacao
    echo desligada na BIOS?^). Depois rode este arquivo de novo.
    pause
    exit /b 1
)
goto esperar_docker

:docker_pronto
echo [2/3] Docker pronto.

REM --- 2. Escolhe qual docker-compose.yml usar ---
set "PASTA_DOCKER_COMPOSE="
for /d %%A in ("%PASTA_AQUI%..\Automatiza*") do (
    for /d %%D in ("%%A\Backend*") do (
        if exist "%%D\docker-compose.yml" set "PASTA_DOCKER_COMPOSE=%%D"
    )
)
if not defined PASTA_DOCKER_COMPOSE (
    for /d %%A in ("%BASE_GITHUB%\Automatiza*") do (
        for /d %%D in ("%%A\Backend*") do (
            if exist "%%D\docker-compose.yml" set "PASTA_DOCKER_COMPOSE=%%D"
        )
    )
)

REM --- 3. Sobe o Postgres + Adminer ---
if defined PASTA_DOCKER_COMPOSE (
    echo [3/3] Subindo os containers com o docker-compose.yml do pipeline-laudos-banco-b:
    echo       %PASTA_DOCKER_COMPOSE%
    pushd "%PASTA_DOCKER_COMPOSE%"
    REM o "|| set" roda na hora certa; %ERRORLEVEL% dentro de parenteses nao
    docker compose up -d || set "FALHOU=1"
    popd
) else (
    echo [3/3] pipeline-laudos-banco-b nao encontrado nesta maquina - subindo os
    echo       containers com o docker-compose.yml desta pasta.
    pushd "%PASTA_AQUI%"
    docker compose -p %PROJETO_COMPOSE% up -d || set "FALHOU=1"
    popd
)

if defined FALHOU (
    echo.
    echo [ERRO] O docker compose terminou com erro.
    echo Se a mensagem acima falar que o nome "postgres_pdf" ja esta em uso,
    echo o banco ja foi criado por outro caminho nesta maquina - abra o
    echo Docker Desktop, aba Containers, e de Start nele.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Pronto!
echo ============================================================
echo  Banco (Postgres): localhost:5432  (usuario/senha: postgres/postgres, banco: testdb)
echo  Adminer (interface web): http://localhost:8080
echo ============================================================
pause
