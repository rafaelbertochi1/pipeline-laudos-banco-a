# pipeline-laudos-banco-a

> **Sobre este repositório.** Versão de portfólio de um projeto real, desenvolvido em
> ambiente corporativo em 2026. Os nomes da empresa, dos sistemas e dos bancos, as URLs
> e os dados de exemplo foram trocados por nomes genéricos ("Central de Gestão",
> "Plataforma", "Banco A", "Banco B"), então o código não roda contra os endereços de
> exemplo. O histórico de commits é novo e resumido; o caminho real do projeto está em
> [HISTORICO.md](HISTORICO.md).

Robô de download e extração dos laudos de avaliação de imóveis do **Banco A**
na plataforma [Plataforma](https://plataforma-laudos.example.com), gravando os dados
estruturados no mesmo banco Postgres já usado para os laudos do Banco B
(projeto [pipeline-laudos-banco-b](https://github.com/rafaelbertochi1/pipeline-laudos-banco-b)).

É a mesma pipeline do pipeline-laudos-banco-b, com o cliente trocado: o login,
a navegação e o download na Plataforma são iguais (só muda qual card é
escolhido na tela "escolha o cliente"), e o que muda de verdade é a
leitura do PDF - o laudo do Banco A tem outro layout, por isso o parser é
próprio.

## Resumo rápido

```powershell
# 1. clonar e entrar na pasta
git clone https://github.com/rafaelbertochi1/pipeline-laudos-banco-a.git
cd pipeline-laudos-banco-a

# 2. ambiente virtual + dependências Python
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

# 3. subir o Postgres compartilhado (2 cliques no subir_banco.bat, ou
#    `docker compose up -d` na pasta do pipeline-laudos-banco-b)

# 4. credenciais do Plataforma (uma vez por computador - ver seção Login;
#    pule se já configurou pro robô do Banco B ou pro Cadastro_Gestao)

# 5. pipeline completo (download + extração + imagens), sem tela
python rodar_pipeline.py
```

## Roteiro pra primeira rodada no PC principal

Na ordem, no PowerShell (terminal do VS Code), dentro da pasta do projeto:

1. `git pull` (ou `git clone`, se for a primeira vez - ver seção 1) e
   ambiente Python (seção 2). Sem `pip install` de novo se o venv já existe.
2. Banco de pé: 2 cliques no `subir_banco.bat`. Confira em
   `http://localhost:8080` que a base `testdb` abre (a tabela `laudos_banco_a`
   só aparece depois da primeira extração).
3. Credenciais da Plataforma no terminal (seção 4): tem que dar `True` nos três.
4. **Rodada de teste com uma semana**: `python rodar_pipeline.py`, período
   de 7 dias recentes. Olhe o resumo do download (quantos "direto pela
   API") e se a extração gravou sem `[BLOQUEADO]`.
5. `python testar_amostra.py`. Veredito "OK" = pode ir pro histórico.
6. **Histórico**: `python rodar_pipeline.py` com o período inteiro
   (ex.: `01/01/2023` a hoje). Ele vai em lotes de um mês. Pode deixar
   rodando; o PC não suspende durante a extração.
7. No fim, `python checar_qualidade_dados.py` e mande o relatório.

Qualquer `[ERRO]`, `[BLOQUEADO]` ou veredito "NÃO RODE": mande o arquivo
de `logs/` correspondente e, se der, 1 ou 2 PDFs citados.

## O que é cada script

| Script | O que faz |
|---|---|
| `banco_a_downloader.py` | Robô com **Playwright** que faz login na Plataforma (cliente Banco A) e baixa os laudos em PDF de um período de datas para `data/laudos/`. Loga sozinho quando a sessão expira (senha + verificação em duas etapas), coleta a lista do período em sub-períodos paralelos e baixa os PDFs direto pela API da Plataforma (vários ao mesmo tempo), caindo pra tela só no que a API não resolver. Laudo checado "sem laudo publicado" não é reaberto a cada execução. É o `plataforma_downloader.py` do Banco B com o cliente trocado. |
| `banco_a_extractor.py` | Lê os PDFs com **pdfplumber** e grava no Postgres via **psycopg2**, na tabela `laudos_banco_a`. Só lê PDF que ainda não está no banco. Chama o `banco_a_imagens.py` a cada PDF novo, então já sai com as fotos extraídas. **Antes de gravar, valida o lote inteiro** com as checagens do `checagens.py`: se alguma passar do limite (ex.: laudos sem endereço - sinal de layout novo), **não grava nada**, mostra o relatório e sai com erro; `$env:VALIDACAO_IGNORAR="1"` grava mesmo assim. Log em `logs/extracao_<data>.txt`. |
| `banco_a_imagens.py` | Extrai fotos de cada laudo PDF (com **pymupdf**) e salva em `data/imagens/`: a fachada (`laudo_<id>_img.<ext>`) e **todas as fotos do "RELATÓRIO FOTOGRÁFICO"**, cada uma com um label tirado da legenda no nome do arquivo: `laudo_<id>_<label>.<ext>` pra primeira de cada label, `_<label>_2`, `_3`... pras seguintes. Labels: sala, quarto, banheiro, cozinha, area_servico, varanda, garagem, quintal, escritorio, closet, despensa, corredor, escada, telhado, medidor, e as áreas comuns e de lazer do prédio (salao_festas, playground, academia, piscina, quadra, churrasqueira, portaria, elevador, area_comum), além de entorno, croqui, mapa, numero e fachada (fachadas extras saem como `fachada_2`...). Legenda que não cai em label nenhum vira `outro` com a legenda embutida no nome (`laudo_<id>_outro_1_closet.jpg`); foto sem legenda embaixo vira `sem_legenda`. A lista de labels é `CATEGORIAS_FOTO` no topo do arquivo, e o resumo da execução mostra as legendas mais comuns que viraram `outro`, pra ir crescendo a lista. Foto repetida no mesmo laudo sai uma vez só; página escaneada inteira não conta como foto. Laudo sem a seção (Laudo Eletrônico) fica só com a fachada, se tiver legenda "Fachada" em alguma página. Roda sozinho (`python banco_a_imagens.py`, em paralelo, um processo por núcleo; `$env:IMAGENS_PROCESSOS="4"` pra usar menos) ou é chamado pelo `banco_a_extractor.py` em toda extração nova. Nunca regrava nem renomeia o que já está na pasta. PDFs já lidos ficam em `data/imagens/_pdfs_verificados.json` (com cópia reserva) e não são reabertos; pra reextrair tudo, apague a pasta `data/imagens` inteira. Mesma lógica do Banco B, **ainda não conferida em PDF real do Banco A** - rodar o `testar_amostra.py` antes da base toda. |
| `rodar_pipeline.py` | Orquestra download + extração + imagens em sequência e mostra quanto tempo cada etapa levou. **Forma recomendada de rodar tudo.** |
| `testar_amostra.py` | **Teste rápido antes de rodar na base inteira.** Roda extração e imagens numa amostra (60 laudos por padrão, 1/3 deles os baixados mais recentemente) sem gravar nada no banco nem em `data/imagens/`, e aponta problema conhecido: campo vazio ou absurdo, texto de outra coluna, foto de cômodo igual à da fachada, sala vinda de área comum. Termina com um veredito ("pode rodar" / "não rode ainda"). Relatório em `logs/teste_rapido_<data>.txt`. |
| `checagens.py` | As checagens de dados usadas pelo extrator (no lote, antes de gravar) e pelo teste rápido, com o limite tolerável de cada uma. |
| `checar_qualidade_dados.py` | Roda de uma vez todas as consultas de qualidade do `.sql` e salva um relatório único em `logs/qualidade_<data>.txt` - campos vazios, valores incoerentes, outliers, cobertura por mês e uma amostra aleatória pra conferir contra o PDF. **Forma recomendada de checar os dados depois de uma extração.** |
| `checar_qualidade_dados.sql` | As consultas de qualidade (lidas pelo script acima), pra quem preferir rodar direto no Postgres via Adminer. |
| `subir_banco.bat` | Atalho Windows (2 cliques): abre o Docker Desktop se precisar, espera ficar pronto e sobe o Postgres + Adminer. Se achar a pasta do pipeline-laudos-banco-b na máquina (ao lado desta ou em `Documents\GitHub`), usa o `docker-compose.yml` de lá; senão usa o desta pasta, com o mesmo nome de projeto, pra os dois repositórios sempre apontarem pro mesmo banco. |
| `docker-compose.yml` | Postgres 15 + Adminer, cópia do do pipeline-laudos-banco-b (mesmos containers `postgres_pdf`/`adminer_pdf`, mesmas credenciais). Só é usado quando o pipeline-laudos-banco-b não está na máquina. |

## O que NÃO é gravado nem versionado

- CPF/CNPJ e nome do proponente (cliente) não são extraídos para o banco -
  só dados do imóvel e da avaliação.
- PDFs baixados (`data/laudos/*.pdf`), fotos extraídas (`data/imagens/`),
  a sessão de login salva (`sessao_plataforma_banco_a.json`), os caches do
  downloader (`data/laudos/*.json`) e os logs de execução (`logs/`) ficam
  só na sua máquina - estão no `.gitignore` e nunca devem ser commitados,
  pois contêm dados confidenciais de clientes (CPF, nome, endereço, fotos
  do imóvel). Confira com `git status` antes de qualquer commit.

## Pré-requisitos

- Python 3.11 ou 3.12 (64-bit), com "Add python.exe to PATH" marcado na
  instalação.
- Docker Desktop com WSL 2 (para o Postgres). Numa máquina que já roda o
  pipeline-laudos-banco-b, **este projeto reaproveita o mesmo container**, não
  sobe um novo; numa máquina nova, ele sobe o banco sozinho.
- Uma conta com acesso à Plataforma (cliente Banco A).

## Passo a passo

### 1. Clonar o repositório

```powershell
git clone https://github.com/rafaelbertochi1/pipeline-laudos-banco-a.git
cd pipeline-laudos-banco-a
```

### 2. Criar o ambiente Python e instalar as dependências

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

| Pacote | Para quê |
|---|---|
| `playwright` | automação do navegador (`banco_a_downloader.py`) |
| `pdfplumber` | lê os PDFs dos laudos (`banco_a_extractor.py`) |
| `psycopg2-binary` | grava no Postgres |
| `pymupdf` (`fitz`) | extrai as fotos dos PDFs (`banco_a_imagens.py`) |

### 3. Garantir que o Postgres está rodando

**No Windows**, dê dois cliques no `subir_banco.bat`: ele abre o Docker
Desktop (se não estiver aberto), espera ficar pronto e sobe os
containers sozinho. Se a pasta do pipeline-laudos-banco-b existir na máquina
(ao lado desta ou em `Documents\GitHub`), ele usa o `docker-compose.yml`
de lá; se não existir, usa o desta pasta. Nos dois casos é o mesmo banco.

Ou manualmente, nesta pasta:

```powershell
docker compose -p backendlscriptextraolaudos up -d
```

(O `-p` dá ao projeto o mesmo nome que o Compose daria pra pasta do
Banco B, então se o pipeline-laudos-banco-b for clonado depois nesta máquina
ele encontra o banco já criado em vez de tentar subir um segundo.)

**Primeira vez num PC novo**: o Docker Desktop no Windows precisa do WSL 2.
Se ele abrir reclamando "WSL not installed", rode num PowerShell como
administrador e reinicie o computador:

```powershell
wsl --install --no-distribution
```

**Duas máquinas rodando ao mesmo tempo**: cada uma tem o seu Postgres,
então os laudos ficam divididos. Divida o trabalho por período ou por
banco pra não baixar a mesma coisa duas vezes; no fim dá pra juntar tudo
num banco só com `pg_dump` de um e restore no outro (a chave única por
`codigo_laudo` impede duplicar).

Isso sobe o container `postgres_pdf` na porta `5432` (usuário/senha
`postgres`/`postgres`, banco `testdb`) - o mesmo banco onde a tabela
`laudos` (Banco B) já existe. Este projeto só se conecta nele, via as
mesmas variáveis de ambiente:

| Variável | Padrão      | Descrição                    |
|----------|-------------|-------------------------------|
| `PGURL`  | `127.0.0.1` | Host do Postgres              |
| `PGNAME` | `testdb`    | Nome do banco                 |
| `PGUSR`  | `postgres`  | Usuário                       |
| `PGPASS` | `postgres`  | Senha                         |
| `PGPORT` | `5432`      | Porta                         |

Se o seu Postgres usa outras credenciais, defina as variáveis antes de
rodar, por exemplo:

```powershell
$env:PGPASS="sua_senha_aqui"
```

### 4. Login no Plataforma

O robô guarda a sessão em `sessao_plataforma_banco_a.json` (raiz da pasta,
**nunca vai pro Git**; arquivo separado do do Banco B, pra um não
sobrescrever a sessão do outro). Quando ela expira, ele **loga sozinho**:
preenche usuário e senha e responde a verificação em duas etapas (MFA)
com o código do App Autenticador. São as **mesmas variáveis** dos robôs
`robo-cadastro-banco-b`/`Banco A` e do `plataforma_downloader.py` do
Banco B - a conta da Plataforma é uma só, o que muda é o cliente
escolhido depois do login. Se você já configurou em qualquer um deles,
neste computador, não precisa fazer nada aqui.

Configurar uma vez por computador (ficam no seu usuário do Windows, nunca
no código):

```powershell
[Environment]::SetEnvironmentVariable("PLATAFORMA_USUARIO", "voce@exemplo.com", "User")
[Environment]::SetEnvironmentVariable("PLATAFORMA_SENHA", "suasenha", "User")
```

A chave do App Autenticador (`PLATAFORMA_CHAVE_AUTENTICADOR`) se configura
com o `configurar_autenticador.py` do repositório
`robo-cadastro-banco-b` (README de lá, seção Login). A Plataforma pede
MFA em toda sessão nova, sem opção de "lembrar este dispositivo", então
sem essa chave o login automático para na verificação.

Depois de configurar, **feche e reabra o VS Code inteiro** (variável nova
não aparece em terminal já aberto). Pra conferir sem mostrar os valores:

```powershell
"USUARIO: $([bool]$env:PLATAFORMA_USUARIO)  SENHA: $([bool]$env:PLATAFORMA_SENHA)  CHAVE: $([bool]$env:PLATAFORMA_CHAVE_AUTENTICADOR)"
```

Tem que aparecer `True` nos três.

Sem as variáveis, ou se o login automático falhar, dá pra logar na mão
com a janela visível:

```powershell
$env:HEADLESS="0"
python banco_a_downloader.py
$env:HEADLESS=""
```

O terminal avisa quando é sua vez de agir ("A AÇÃO É SUA AGORA"). Quando
o login falha de verdade, o robô salva um print da tela em
`logs/falha_login_plataforma_<motivo>_<data>.png`.

> Antes, este robô vinha com `HEADLESS = False` fixo no arquivo (janela
> sempre visível) e login só manual. Agora o padrão é sem tela, como no
> Banco B, e a janela se liga por variável de ambiente - editar o `.py`
> travava o `git pull`.

### 5. Baixar os laudos históricos

```powershell
python banco_a_downloader.py
```

O robô pede a data inicial e final do período (dd/mm/aaaa). Pra puxar o
histórico, **comece com um período curto** (uma semana) pra confirmar que
tudo passa - login, seleção do cliente, download pela API. Depois pode
dar o período inteiro de uma vez (ex.: `01/01/2023` a hoje): ele quebra
em **lotes de até 31 dias** e processa um de cada vez, conferindo a
sessão antes de cada lote (uma rodada de anos leva horas e o login pode
vencer no meio; ele renova sozinho). Se parar no meio, o resumo diz de
que data continuar. Laudo já baixado é pulado, então repetir um período
não custa nada além da coleta.

Pra não ter que digitar as datas (ex.: deixar rodando de noite):

```powershell
$env:PERIODO_INICIO="01/01/2023"; $env:PERIODO_FIM="31/12/2023"
python rodar_pipeline.py
$env:PERIODO_INICIO=""; $env:PERIODO_FIM=""
```

O resumo no fim diz quantos vieram direto pela API e quantos pela tela,
quantos ainda não têm laudo publicado e quanto tempo levou cada parte.
Se aparecer "Não achei o código dos laudos nos dados da grade - vai tudo
pela tela", avise: o download continua funcionando, só mais lento.

Variáveis de ambiente do downloader (todas opcionais):

| Variável | Padrão | O que faz |
|---|---|---|
| `HEADLESS` | `1` | `0` (ou `false`) abre o navegador visível |
| `PARALELISMO` | `6` | abas simultâneas na coleta e no download (máx. 12). Se a Plataforma ficar lenta ou devolver erro com mais abas, volte pro padrão |
| `DOWNLOADS_SIMULTANEOS` | `12` | quantos PDFs baixar pela API ao mesmo tempo (máx. 24). Se a Plataforma começar a devolver erro, diminua (ex.: `$env:DOWNLOADS_SIMULTANEOS="6"`) |
| `DOWNLOAD_API` | `1` | `0` = baixa tudo pela tela, como antes (mais lento) |
| `DIAS_POR_LOTE` | `31` | tamanho máximo de cada lote de datas (`0` = período inteiro de uma vez). Lote menor = filtros mais estreitos na Plataforma, que foi o que evitou perder laudos no Banco B |
| `PERIODO_INICIO` / `PERIODO_FIM` | — | datas em dd/mm/aaaa; com as duas definidas o robô não pergunta nada no terminal |
| `RECHECAR_SEM_LAUDO_DIAS` | `7` | laudo checado "sem laudo publicado" só é aberto de novo depois desses dias (`0` = checar sempre). A lista fica em `data/laudos/_sem_laudo_publicado.json` |
| `PLATAFORMA_USUARIO` / `PLATAFORMA_SENHA` / `PLATAFORMA_CHAVE_AUTENTICADOR` | — | login automático (seção 4) |

### 6. Extrair os dados para o banco

```powershell
python banco_a_extractor.py
```

Processa os PDFs de `data/laudos/` que ainda não estão no banco e grava
na tabela `laudos_banco_a` (identificados pelo código do laudo, ex:
`INS1001`). Reprocessar um laudo **atualiza** o registro em vez de
duplicar. Na mesma passada salva as fotos de cada laudo em `data/imagens/`.

| Variável | Padrão | O que faz |
|---|---|---|
| `FORCAR_REPROCESSAR` | `0` | `1` relê também os PDFs já gravados (ex.: depois de um ajuste no parser, pra corrigir os dados antigos) |
| `VALIDACAO_IGNORAR` | `0` | `1` grava mesmo se a validação do lote passar do limite |
| `IMAGENS_PROCESSOS` | nº de núcleos | processos em paralelo na etapa de imagens |

> Antes, o extractor relia **todos** os PDFs a cada execução. Com a base
> histórica (dezenas de milhares de laudos) isso custaria horas por
> rodada, então agora ele pula o que já está no banco; o
> `FORCAR_REPROCESSAR=1` faz o que a versão antiga fazia sempre.

### 7. Testar antes de rodar na base inteira

Depois de um `git pull` que mudou a extração (antes de reprocessar com
`FORCAR_REPROCESSAR`), ou depois de baixar meses novos (antes de extrair):

```powershell
python testar_amostra.py
```

Leva 1–2 minutos e não mexe no banco. Se o veredito no final for "NÃO
RODE NA BASE INTEIRA AINDA", mande o relatório (`logs/teste_rapido_*.txt`)
e 1 ou 2 dos PDFs citados antes. Pra uma amostra maior: `$env:TESTE_QTD="150"`.

### 8. Conferir os dados

Acesse o Adminer em `http://localhost:8080` (sistema PostgreSQL, servidor
`postgres`, usuário/senha `postgres`, base `testdb`) e consulte a tabela
`laudos_banco_a`. Ou gere o relatório de qualidade:

```powershell
python checar_qualidade_dados.py
```

Ele roda todas as consultas de `checar_qualidade_dados.sql` de uma vez e
salva o resultado em `logs/qualidade_<data>.txt`.

## Estrutura da tabela `laudos_banco_a`

Mesmas colunas da tabela `laudos` (Banco B) de quando esta tabela foi
criada: `numero_proposta`, `codigo_laudo` (chave única), `data_avaliacao`,
`endereco`, `numero`, `complemento`, `tipo_imovel`, `area_privativa_m2`,
`area_comum_m2`, `area_total_m2`, `area_nao_averbada_m2`,
`area_terreno_m2`, `quartos`, `suites`, `banheiros`, `vagas`,
`idade_anos`, `padrao_acabamento`, `estado_conservacao`, `valor_mercado`,
`valor_venda_forcada` (sempre `0` - o laudo do Banco A não tem esse
conceito), `valor_unitario_m2` (calculado: valor / área privativa, ou /
área de terreno em lote), `coordenadas`, `latitude`, `longitude`, `path`,
`modelo_usado` (`fisico` ou `eletronico`).

A `laudos` do Banco B ganhou colunas depois (bairro, município, UF,
CEP, matrícula, metodologia, infraestrutura, origem da coordenada) que
aqui **não** foram criadas - entram quando forem pedidas.

O parser foi validado com exemplos reais dos dois modelos de laudo do
Banco A (Físico/Casa e Eletrônico/Apartamento). Outras combinações (ex:
Apartamento Físico, Casa Eletrônico, laudo de terreno, laudos antigos do
histórico) podem precisar de ajustes nos regex de `banco_a_extractor.py` - é
pra isso que o `testar_amostra.py` e a validação do lote existem: se o
layout mudar, eles barram antes de gravar lixo.

## O que veio do Banco B e o que ficou de fora

Trazido do pipeline-laudos-banco-b (commits de 11/09 a 30/09/2026): login
automático com MFA, download pela API com fila única, coleta em
sub-períodos paralelos, cache de "sem laudo publicado", extrator que pula
o que já está no banco e valida o lote antes de gravar, fotos de cômodos
com cache de PDFs verificados, pipeline orquestrado, teste rápido e
relatório de qualidade.

Ficou de fora, de propósito:

- **Coordenadas pelo endereço** (`banco_b_geocodificar.py`): precisa de
  município/UF/CEP no banco, que a `laudos_banco_a` não tem; e o laudo do
  Banco A imprime "Coordenadas do Imóvel" (a consulta "sem_coordenada" do
  relatório de qualidade mostra quantos ficaram sem).
- **Conferência contra a segunda fonte do PDF** (`conferir_extracao.py`)
  e a tabela de **amostras** (`laudos_amostras`): dependem das seções de
  cálculo do laudo do Banco B, que o do Banco A não tem no mesmo formato.
- Regras específicas do layout Banco B: capa de 2023, laudo de
  "Múltiplas Unidades" fora do banco, checkboxes do modelo digital.

## Problemas comuns

- **"Nao foi possivel subir o Docker"** — o Docker Desktop precisa estar
  aberto e com o motor rodando.
- **"A sessão salva expirou e não consegui logar sozinho"** — faltam
  `PLATAFORMA_USUARIO`/`PLATAFORMA_SENHA` neste terminal, ou estão erradas
  (seção 4). Se você acabou de configurar, feche e reabra o VS Code.
- **Linhas `[MFA]` e o robô para** — a senha foi aceita, mas falta
  `PLATAFORMA_CHAVE_AUTENTICADOR` (seção 4). Se aparecer "código
  recusado", confira o relógio do Windows (Configurações → Hora →
  Sincronizar agora).
- **"[BLOQUEADO] Nada foi gravado no banco"** — a validação do lote achou
  laudos demais com campo vazio (provável layout novo). Mande o log
  `logs/extracao_<data>.txt` e um dos PDFs citados.
- **Playwright reclama que não achou o navegador** — rode
  `playwright install chromium` de novo.
- **Adminer não conecta no banco** — no campo "Servidor", use `postgres`
  (nome do serviço no `docker-compose.yml`), não `localhost`.
