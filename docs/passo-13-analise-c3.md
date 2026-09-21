# Passo 13 - Análise do C3

**Status: concluído.**

Este documento encerra o cenário C3 com as quatro tentativas oficiais já
preservadas. Não foram feitas novas execuções nem alterações retroativas nos
resultados. A síntese de desempenho usa exclusivamente as duas tentativas
válidas e, por isso, toda estatística agregada declara `n=2`.

## Objetivo

O C3 avalia a proteção de entrada por rate limiting antes de uma API com uma
réplica e um worker com uma réplica. A análise separa quatro aspectos: atuação
do limite por respostas HTTP 429, desempenho das execuções válidas,
estabilidade e repetibilidade arquitetural e ocorrência de falhas inesperadas.

## Configuração do Kong e perfil de carga

O gateway usou Kong `3.9.1-ubuntu` em modo DB-less, com uma réplica, política
local, limitação por serviço e janela de um segundo. O limite anunciado e
validado no preflight foi de exatamente 20 requisições por segundo. O perfil
`ramping-arrival-rate` foi mantido em quatro estágios: 20 req/s por 20 s, 22
req/s por 30 s, 22 req/s por 30 s e redução a zero por 10 s.

A resposta HTTP 429 representa rejeição intencional na entrada. Ela demonstra
que a proteção foi aplicada, mas não é evidência suficiente, isoladamente, de
estabilidade da API ou de repetibilidade do conjunto.

## Quadro das tentativas oficiais

| Tentativa | Validade | Estabilidade | 2xx | 429 | 5xx | Erros de conexão | Falhas inesperadas | Descartes | Reinícios da API | Agregado válido |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `c3-run-1` | VALID | STABLE | 1.518 | 91 | 0 | 0 | 0 | 0 | 0 | sim |
| `c3-run-2` | VALID | STABLE | 1.518 | 91 | 0 | 0 | 0 | 0 | 0 | sim |
| `c3-run-3` | INVALID | UNSTABLE | 1.349 | 91 | 19 | 0 | 19 | 46 | 1 | não |
| `c3-run-3-retry-1` | INVALID | UNSTABLE | 1.009 | 58 | 56 | 3 | 59 | 250 | 1 | não |

As quatro tentativas registraram respostas 429 e, portanto, evidenciaram a
atuação do rate limiting. A validade experimental e a estabilidade
arquitetural são avaliadas separadamente dessa proteção.

## Resultados das execuções válidas

| Métrica | `c3-run-1` | `c3-run-2` | Síntese descritiva (`n=2`) |
| --- | ---: | ---: | ---: |
| Requisições iniciadas | 1.609 | 1.609 | média 1.609 |
| Respostas 2xx | 1.518 | 1.518 | média 1.518 |
| Respostas 429 | 91 | 91 | média 91 |
| Percentual 429 | 5,656% | 5,656% | média 5,656% |
| Throughput total | 17,878732 req/s | 17,878702 req/s | média 17,878717 req/s |
| Throughput aceito | 16,867567 req/s | 16,867538 req/s | média 16,867553 req/s |
| Throughput rejeitado | 1,011165 req/s | 1,011163 req/s | média 1,011164 req/s |
| Latência geral p95 | 65,611 ms | 74,852 ms | média 70,2315 ms |
| Latência geral p99 | 100,485 ms | 104,884 ms | média 102,6845 ms |
| Latência 2xx p95 | 66,042 ms | 77,717 ms | média 71,8795 ms |
| Latência 2xx p99 | 102,941 ms | 105,967 ms | média 104,454 ms |
| Drenagem | 4 s | 3 s | média 3,5 s |

Nas duas execuções válidas, as 1.518 respostas 2xx reconciliaram exatamente
com 1.518 pedidos criados e 1.518 pedidos concluídos. Não restaram pedidos
pendentes, em processamento ou com falha. Também não houve respostas 5xx,
erros de conexão, falhas inesperadas, iterações descartadas ou reinicializações
da API, do gateway ou do worker. O backlog máximo foi um.

Média, mediana, desvio-padrão amostral, mínimo e máximo estão em
`results/consolidated/c3-aggregate-summary.csv`. Esses valores são uma síntese
descritiva de somente duas observações e não devem ser interpretados como
estimativas estatísticas fortes. Percentis agregados resumem os percentis de
cada execução; as amostras brutas não foram combinadas.

## Tentativas inválidas preservadas

### `c3-run-3`

A API reiniciou uma vez durante a carga. A tentativa registrou 19 respostas
5xx, 19 falhas inesperadas e 46 iterações descartadas. O p95 geral subiu para
4.661,574 ms e o p99 para 31.155,653 ms. Por isso, a execução permanece
`INVALID / UNSTABLE` e não participa das médias de desempenho.

### `c3-run-3-retry-1`

A repetição apresentou novamente uma reinicialização da API, acompanhada por
56 respostas 5xx, três erros de conexão, 59 falhas inesperadas e 250 iterações
descartadas. O p95 geral foi 27.674,417 ms e o p99 55.204,899 ms. A recorrência
mantém a tentativa como `INVALID / UNSTABLE` e fornece evidência de
repetibilidade e robustez insuficientes da configuração isolada.

As duas tentativas inválidas permanecem visíveis em arquivos, CSVs e planilha,
mas não são misturadas aos agregados das execuções válidas.

## Encerramento das repetições

Não haverá nova repetição do C3. As duas primeiras execuções estáveis e as duas
tentativas subsequentes com a mesma classe de falha constituem o conjunto
oficial encerrado. Parar nesse ponto evita seleção oportunista de uma nova
execução que pudesse favorecer uma conclusão diferente.

## Conclusão metodológica

O mecanismo de rate limiting demonstrou capacidade de proteção da entrada por
meio de respostas HTTP 429 e apresentou desempenho estável em duas execuções
válidas. Entretanto, a ocorrência repetida de reinicialização da API nas
tentativas subsequentes mostrou que a proteção isolada não garantiu
estabilidade e repetibilidade suficientes sob todas as execuções observadas.

Essa conclusão não caracteriza fracasso do rate limiting: ele cumpriu sua
função de rejeitar parte da carga excedente. O limite de entrada e a robustez da
arquitetura são propriedades distintas, e os dados do C3 exigem que ambas sejam
reportadas separadamente.

## Ligação com o C4

O achado fundamenta a avaliação posterior de uma estratégia combinada no C4,
na qual proteção de entrada e capacidade arquitetural podem ser estudadas em
conjunto. Essa continuidade não altera retroativamente o protocolo, a
classificação ou os resultados do C3.

## Evidências consolidadas

O script `scripts/consolidate-c3-results.py` valida os quatro diretórios
oficiais, reconcilia totais gerais e por estágio e regenera os quatro CSVs e a
planilha sem edição manual. A planilha contém as abas `Resumo das execuções`,
`Resultados por estágio`, `Agregados válidos`, `Rate limiting`, `Tentativas
inválidas` e `Metodologia`, mantendo as tentativas excluídas em seção própria.
