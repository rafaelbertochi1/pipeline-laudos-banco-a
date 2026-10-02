# Histórico do projeto

Este repositório é a versão de portfólio de um projeto real, desenvolvido em
ambiente corporativo entre 10/09/2026 e 02/10/2026. O repositório original é
privado e teve 19 commits de trabalho nesse período.

O histórico de commits que você vê aqui é **novo**: o código foi publicado em
poucos commits, um por componente, depois de anonimizado. Este arquivo conta
como o projeto de fato evoluiu.

## O que foi anonimizado

- Nome da empresa e do seu sistema interno: aparece como "Central de Gestão".
- Sistema de laudos de terceiros: aparece como "Plataforma".
- Bancos clientes: "Banco A" e "Banco B".
- URLs e endpoints: trocados por endereços `example.com`.
- Números de proposta e outros dados que apareciam em comentários e exemplos:
  trocados por valores fictícios.

Por causa disso o código não roda contra os endereços de exemplo. A lógica, a
estrutura e a documentação são as do projeto real.

## Como o projeto evoluiu

### 1. Primeira versão (10/09 a 11/09)

O projeto nasceu como irmão do
[pipeline-laudos-banco-b](https://github.com/rafaelbertochi1/pipeline-laudos-banco-b):
o login, a navegação e o download na Plataforma são os mesmos, e o que muda é
a leitura do PDF, porque o laudo do Banco A tem outro layout. Nessa fase:

- robô de download e extrator próprio para o layout do Banco A;
- fluxo de download validado contra o sistema real;
- correção da área privativa zerada no modelo eletrônico;
- área de terreno, área não averbada e valor unitário calculado;
- `subir_banco.bat`, para subir o Postgres compartilhado com dois cliques.

### 2. Primeira imagem (17/09)

- extração da foto da fachada de cada laudo.

### 3. Paridade com o pipeline do Banco B (01/10)

As melhorias amadurecidas no projeto irmão foram trazidas de uma vez:

- login automático com verificação em duas etapas;
- download pela API e coleta em sub-períodos;
- execução em lotes de até 31 dias, com o período por variável de ambiente;
- validação do lote antes de gravar;
- todas as fotos do relatório fotográfico, nomeadas pela legenda;
- `rodar_pipeline.py`, `testar_amostra.py` e o relatório de qualidade.

### 4. Ajustes do layout do Banco A (02/10)

- leitura da legenda das fotos em página girada;
- tipo de imóvel e estado com mais de uma palavra, e coordenadas em outros
  formatos;
- laudos de inspeção deixaram de ser gravados no lugar do laudo de avaliação.

## Projetos relacionados

- [pipeline-laudos-banco-b](https://github.com/rafaelbertochi1/pipeline-laudos-banco-b):
  o pipeline original, de onde este foi derivado.
- [robo-cadastro-banco-a](https://github.com/rafaelbertochi1/robo-cadastro-banco-a) e
  [robo-cadastro-banco-b](https://github.com/rafaelbertochi1/robo-cadastro-banco-b):
  robôs que levam o status das inspeções para o sistema de gestão.
