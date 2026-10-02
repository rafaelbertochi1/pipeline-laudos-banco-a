-- Diagnóstico de qualidade dos dados extraídos (tabela `laudos_banco_a`).
--
-- Jeito rápido: rodar `python checar_qualidade_dados.py`, que executa
-- TODAS as consultas daqui de uma vez e salva um relatório único em
-- logs/qualidade_<data>.txt.
--
-- Jeito manual: colar cada bloco separadamente no Adminer (aba "Comando
-- SQL") - alguns clientes só mostram o resultado da ÚLTIMA consulta
-- quando várias são coladas juntas.
--
-- A ideia: a extração quase nunca dá erro visível. Quando uma leitura
-- falha, a linha grava normal, só que com campo vazio, zero ou texto
-- contaminado por pedaço de outra coluna do PDF. As consultas abaixo
-- caçam exatamente essas linhas.
--
-- Formato: cada consulta começa com "-- @@ <título>", seguido de linhas
-- "--" de comentário (que viram a nota "Esperado" no relatório). O
-- checar_qualidade_dados.py depende desse formato pra separar os blocos.
--
-- Versão pro Banco A do checar_qualidade_dados.sql do pipeline-laudos-banco-b:
-- sem as consultas de amostras, município/UF, metodologia e venda forçada
-- (a laudos_banco_a não tem essas colunas).

-- @@ Panorama dos laudos: campos vazios por modelo
-- Esperado: tudo perto de zero, menos idade_zero (imóvel novo tem idade
-- 0 de verdade) e sem_terreno (só casa/lote têm área de terreno). O
-- parser devolve "Apartamento"/"Normal"/"Bom" quando NÃO acha o campo,
-- então tipo/padrão/estado vazios nunca aparecem aqui - ver as consultas
-- de valores encontrados logo abaixo.
SELECT modelo_usado,
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE codigo_laudo IS NULL)      AS sem_codigo,
  COUNT(*) FILTER (WHERE numero_proposta = '')      AS sem_proposta,
  COUNT(*) FILTER (WHERE endereco = '')             AS sem_endereco,
  COUNT(*) FILTER (WHERE valor_mercado = 0)         AS valor_zero,
  COUNT(*) FILTER (WHERE area_privativa_m2 = 0
                     AND area_terreno_m2 = 0)       AS sem_area,
  COUNT(*) FILTER (WHERE area_terreno_m2 = 0)       AS sem_terreno,
  COUNT(*) FILTER (WHERE idade_anos = 0)            AS idade_zero,
  COUNT(*) FILTER (WHERE data_avaliacao IS NULL)    AS sem_data,
  COUNT(*) FILTER (WHERE latitude IS NULL)          AS sem_coordenada
FROM laudos_banco_a
GROUP BY modelo_usado;

-- @@ Tipos de imóvel encontrados
-- Esperado: poucas linhas, todas com nome de imóvel de verdade
-- (Apartamento, Casa, Terreno...). Valor com número no meio ou frase
-- solta = leitura pegou a coluna errada.
SELECT tipo_imovel, COUNT(*) AS qtd
FROM laudos_banco_a GROUP BY 1 ORDER BY 2 DESC LIMIT 25;

-- @@ Padrões de acabamento encontrados
-- Esperado: Normal, Alto, Médio, Baixo, Simples...
SELECT padrao_acabamento, COUNT(*) AS qtd
FROM laudos_banco_a GROUP BY 1 ORDER BY 2 DESC LIMIT 25;

-- @@ Estados de conservação encontrados
-- Esperado: Bom, Regular, Novo, Ótimo, Ruim...
SELECT estado_conservacao, COUNT(*) AS qtd
FROM laudos_banco_a GROUP BY 1 ORDER BY 2 DESC LIMIT 25;

-- @@ Código de laudo duplicado (dois PDFs com o mesmo código)
-- Esperado: nenhuma linha.
SELECT codigo_laudo, COUNT(*) AS qtd, STRING_AGG(path, ', ') AS arquivos
FROM laudos_banco_a
GROUP BY codigo_laudo HAVING COUNT(*) > 1 LIMIT 15;

-- @@ Datas fora do padrão dd/mm/aaaa ou de ano improvável
-- Esperado: nenhuma linha.
SELECT path, codigo_laudo, data_avaliacao
FROM laudos_banco_a
WHERE data_avaliacao IS NOT NULL
  AND (data_avaliacao !~ '^\d{2}/\d{2}/\d{4}$'
       OR RIGHT(data_avaliacao, 4) NOT BETWEEN '2015' AND '2026')
LIMIT 15;

-- @@ Quantos laudos com valor por m² que não bate com valor / área
-- Esperado: zero - aqui o valor por m² é sempre calculado pelo extrator
-- (valor / área privativa, ou / área de terreno). Linha aqui = gravado
-- por uma versão antiga; FORCAR_REPROCESSAR=1 corrige.
SELECT COUNT(*) AS laudos_com_unitario_incoerente
FROM laudos_banco_a
WHERE valor_unitario_m2 > 0
  AND ABS(valor_unitario_m2 - valor_mercado / NULLIF(COALESCE(NULLIF(area_privativa_m2,0),
          NULLIF(area_terreno_m2,0)),0)) > valor_unitario_m2 * 0.02;

-- @@ Quantos laudos com valor, área, idade ou cômodos fora da realidade
-- Esperado: número baixo.
SELECT
  COUNT(*) FILTER (WHERE valor_mercado > 0 AND valor_mercado < 10000) AS valor_baixo_demais,
  COUNT(*) FILTER (WHERE valor_mercado > 50000000)                    AS valor_alto_demais,
  COUNT(*) FILTER (WHERE area_privativa_m2 > 10000)                   AS area_priv_absurda,
  COUNT(*) FILTER (WHERE area_terreno_m2 > 100000)                    AS area_terreno_absurda,
  COUNT(*) FILTER (WHERE idade_anos > 100)                            AS idade_absurda,
  COUNT(*) FILTER (WHERE quartos > 15)                                AS quartos_demais,
  COUNT(*) FILTER (WHERE banheiros > 15)                              AS banheiros_demais,
  COUNT(*) FILTER (WHERE vagas > 30)                                  AS vagas_demais
FROM laudos_banco_a;

-- @@ Exemplos de laudos com número fora da realidade
SELECT path, tipo_imovel, valor_mercado, area_privativa_m2, area_terreno_m2,
       idade_anos, quartos, banheiros, vagas
FROM laudos_banco_a
WHERE (valor_mercado > 0 AND valor_mercado < 10000) OR valor_mercado > 50000000
   OR area_privativa_m2 > 10000 OR area_terreno_m2 > 100000
   OR idade_anos > 100 OR quartos > 15 OR banheiros > 15 OR vagas > 30
LIMIT 15;

-- @@ Coordenadas fora do Brasil
-- Esperado: nenhuma linha (latitude entre -34 e 6, longitude entre -74 e -34).
SELECT path, codigo_laudo, latitude, longitude
FROM laudos_banco_a
WHERE latitude IS NOT NULL
  AND (latitude NOT BETWEEN -34 AND 6 OR longitude NOT BETWEEN -74 AND -34)
LIMIT 15;

-- @@ Laudos por mês de avaliação
-- Esperado: a cobertura do período baixado, sem buraco de meses no meio.
SELECT RIGHT(data_avaliacao, 4) || '-' || SUBSTRING(data_avaliacao, 4, 2) AS mes,
       COUNT(*) AS laudos
FROM laudos_banco_a
WHERE data_avaliacao ~ '^\d{2}/\d{2}/\d{4}$'
GROUP BY 1 ORDER BY 1;

-- @@ 12 laudos aleatórios pra conferir contra o PDF
-- Abra o PDF (coluna path) e compare campo a campo.
SELECT path, modelo_usado, tipo_imovel, endereco, numero, area_privativa_m2,
       area_terreno_m2, quartos, banheiros, vagas, idade_anos, valor_mercado,
       valor_unitario_m2, data_avaliacao
FROM laudos_banco_a
ORDER BY random() LIMIT 12;
