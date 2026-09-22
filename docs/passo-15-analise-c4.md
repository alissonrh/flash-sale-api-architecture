# Passo 15 — Análise do C4

**Status: concluído.**

Este documento apresenta uma análise técnica e descritiva do cenário C4, no
qual o HPA da API e o rate limiting do Kong foram ativados simultaneamente. O
texto organiza as evidências para apoiar a futura redação do TCC, mas não deve
ser interpretado como sua versão definitiva nem como demonstração causal ou
inferência estatística para outros ambientes.

## Base de evidências e abordagem

A análise usa exclusivamente os resultados versionados e consolidados em
`results/consolidated/c1-*`, `results/consolidated/c2-*`,
`results/consolidated/c3-*` e `results/consolidated/c4-*`, além dos documentos
`docs/passo-9-analise-c1.md`, `docs/passo-13-analise-c3.md` e
`docs/passo-14-c4-hpa-rate-limiting.md`.

As estatísticas do C4 descrevem as três execuções oficiais válidas (`n=3`).
Média, mediana, desvio-padrão amostral, mínimo e máximo são calculados entre os
valores de cada execução. Em particular, p95 e p99 são percentis já calculados
por execução: os agregados resumem esses três percentis e não recomputam uma
distribuição mediante a união das amostras brutas.

## 1. Objetivo e hipótese

O objetivo do C4 é avaliar o funcionamento combinado de duas propriedades
distintas:

- **proteção de entrada**, exercida pelo Kong ao limitar a admissão a 20
  requisições por segundo e responder com HTTP 429 ao excesso; e
- **capacidade de processamento**, ampliada pelo HPA quando a utilização de CPU
  da API justifica aumentar a quantidade de Pods de uma até cinco réplicas.

A hipótese experimental estabelecia que o Kong produziria rejeições 429
controladas enquanto o HPA poderia escalar a API em resposta à carga aceita.
As respostas 429 eram resultados previstos do tratamento, não falhas. Falhas
inesperadas continuavam sendo respostas 5xx, erros de conexão, outros status ou
inconsistências de execução. A hipótese não garantia previamente que a escala
ocorreria nem que os mecanismos eliminariam saturação, reinicializações ou
variabilidade de latência.

## 2. Validade, estabilidade e inclusão

As três execuções oficiais satisfizeram os critérios de validade do protocolo:
configuração congelada de carga, HPA e gateway; classificação HTTP exaustiva;
atuação observável do rate limiting; reconciliação entre respostas 2xx e
pedidos; drenagem concluída; e disponibilidade dos artefatos exigidos. Por isso,
as três integram os agregados, inclusive quando há uma observação operacional
desfavorável.

Validade experimental e estabilidade operacional não são sinônimos. O
`c4-run-1` é `VALID / UNSTABLE` porque registrou uma reinicialização real do
gateway. Excluí-lo removeria uma característica observada da configuração e
enviesaria a síntese. O `c4-run-2` é `VALID / STABLE`: sua latência elevada não
violou os critérios de validade nem veio acompanhada de reinicializações,
falhas HTTP inesperadas ou drenagem incompleta. O `c4-run-3` é
`VALID / STABLE`.

## 3. Resumo dos três ensaios oficiais

| Execução | Validade / estabilidade | Requisições | 2xx | 429 (%) | Throughput total / aceito / rejeitado (req/s) | Latência geral p95 / p99 (ms) | API observada | Primeira decisão de escala | Reinicializações | Drenagem |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| `c4-run-1` | VALID / UNSTABLE | 1.609 | 1.519 | 90 (5,594%) | 17,878542 / 16,878499 / 1,000043 | 82,621 / 102,642 | 1–5 Pods | 39,346 s | gateway: 1 | 55 s |
| `c4-run-2` | VALID / STABLE | 1.609 | 1.518 | 91 (5,656%) | 17,878546 / 16,867391 / 1,011155 | 3.645,513 / 5.226,974 | 1–5 Pods | 44,516 s | nenhuma | 4 s |
| `c4-run-3` | VALID / STABLE | 1.609 | 1.518 | 91 (5,656%) | 17,878692 / 16,867529 / 1,011163 | 88,009 / 219,522 | 1–5 Pods | 39,369 s | nenhuma | 4 s |

Não houve respostas 5xx, erros de conexão, outros status inesperados, falhas
inesperadas ou iterações descartadas. A classificação fechou em 1.609
requisições em cada execução. Esse resultado descreve apenas as três
observações realizadas e não equivale à garantia de ausência futura de falhas.

## 4. Estatísticas descritivas do C4

As tabelas seguintes reproduzem a síntese de
`results/consolidated/c4-aggregate-summary.csv`. O desvio-padrão é amostral e
todas as linhas usam `n=3`.

### 4.1 Admissão e throughput

| Métrica | Média | Mediana | Desvio-padrão amostral | Mínimo | Máximo |
| --- | ---: | ---: | ---: | ---: | ---: |
| Requisições iniciadas | 1.609,000000 | 1.609 | 0,000000 | 1.609 | 1.609 |
| Respostas 2xx | 1.518,333333 | 1.518 | 0,577350 | 1.518 | 1.519 |
| Respostas 429 | 90,666667 | 91 | 0,577350 | 90 | 91 |
| Respostas 429 (%) | 5,635333 | 5,656 | 0,035796 | 5,594 | 5,656 |
| Throughput total (req/s) | 17,878593 | 17,878546 | 0,000085 | 17,878542 | 17,878692 |
| Throughput aceito (req/s) | 16,871140 | 16,867529 | 0,006374 | 16,867391 | 16,878499 |
| Throughput rejeitado (req/s) | 1,007454 | 1,011155 | 0,006418 | 1,000043 | 1,011163 |

### 4.2 Latência

| Métrica (ms) | Média | Mediana | Desvio-padrão amostral | Mínimo | Máximo |
| --- | ---: | ---: | ---: | ---: | ---: |
| Geral — média da execução | 374,500000 | 40,672 | 579,686857 | 38,964 | 1.043,864 |
| Geral — p95 | 1.272,047667 | 88,009 | 2.055,483039 | 82,621 | 3.645,513 |
| Geral — p99 | 1.849,712667 | 219,522 | 2.925,377893 | 102,642 | 5.226,974 |
| 2xx — média da execução | 396,887667 | 43,028 | 614,445958 | 41,247 | 1.106,388 |
| 2xx — p95 | 1.281,115333 | 89,967 | 2.068,386700 | 83,899 | 3.669,480 |
| 2xx — p99 | 1.853,475667 | 221,551 | 2.928,235284 | 104,837 | 5.234,039 |
| 429 — média da execução | 0,888000 | 0,883 | 0,015133 | 0,876 | 0,905 |
| 429 — p95 | 1,290000 | 1,283 | 0,049870 | 1,244 | 1,343 |
| 429 — p99 | 1,797000 | 1,739 | 0,206211 | 1,626 | 2,026 |

### 4.3 Processamento, recursos, escala e estabilidade

| Métrica | Média | Mediana | Desvio-padrão amostral | Mínimo | Máximo |
| --- | ---: | ---: | ---: | ---: | ---: |
| Pedidos concluídos | 1.518,333333 | 1.518 | 0,577350 | 1.518 | 1.519 |
| Processamento de pedidos p95 (ms) | 272,982000 | 97,068 | 315,608976 | 84,534 | 637,344 |
| Processamento de pedidos p99 (ms) | 354,746333 | 151,339 | 390,245510 | 108,225 | 804,675 |
| Drenagem (s) | 21,000000 | 4 | 29,444864 | 4 | 55 |
| Backlog máximo | 1,000000 | 1 | 0,000000 | 1 | 1 |
| Pico de CPU da API (cores) | 0,459003 | 0,470021 | 0,046537 | 0,407946 | 0,499042 |
| Pico de memória da API (MiB) | 82,925781 | 84,585938 | 4,919842 | 77,390625 | 86,800781 |
| Pico de CPU do gateway (cores) | 0,149043 | 0,047749 | 0,180684 | 0,041729 | 0,357650 |
| Pico de memória do gateway (MiB) | 503,710937 | 505,566406 | 9,109639 | 493,816406 | 511,750000 |
| Tempo até a primeira decisão de escala (s) | 41,077000 | 39,369 | 2,978284 | 39,346 | 44,516 |
| Pods da API — máximo observado | 5,000000 | 5 | 0,000000 | 5 | 5 |
| Réplicas máximas desejadas pelo HPA | 5,000000 | 5 | 0,000000 | 5 | 5 |
| Reinicializações do gateway | 0,333333 | 0 | 0,577350 | 0 | 1 |

As diferenças grandes entre média e mediana em latência, drenagem e CPU do
gateway indicam distribuições assimétricas entre somente três observações. A
média continua sendo reportada, mas não deve ser lida isoladamente.

## 5. Comportamento do rate limiting

O rate limiting atuou nas três execuções. Foram observadas 90 respostas 429 no
`c4-run-1` e 91 nas demais, equivalentes a 5,594%, 5,656% e 5,656% das
requisições iniciadas. A média de 5,635333% representa a fração protegida por
rejeição controlada no conjunto de valores por execução. Essas respostas não
foram classificadas como falhas HTTP inesperadas.

O comportamento por estágio também foi consistente. O primeiro estágio não
teve 429; o segundo teve 29 por execução; o terceiro teve 60; e o quarto teve
uma rejeição no `c4-run-1` e duas nas demais. No terceiro estágio, de alvo 22
req/s, o throughput aceito ficou em aproximadamente 20 req/s e o rejeitado em
2 req/s. Isso é compatível com a atuação observada do limite de 20 req/s, sem
converter essa correspondência em prova de comportamento universal do Kong.

O throughput total foi praticamente invariável, próximo de 17,879 req/s. O
throughput aceito ficou próximo de 16,87 req/s e o rejeitado próximo de 1,01
req/s. A proteção de entrada controla admissão; ela não é, por si só, medida de
capacidade interna nem garantia de estabilidade operacional.

Em cada execução, o total de pedidos criados coincidiu exatamente com as
respostas 2xx: 1.519, 1.518 e 1.518. Todos foram concluídos, sem pedidos
pendentes, em processamento ou com falha. Assim, os consolidados registram
ausência de efeitos colaterais dos 429 no banco: as 272 rejeições observadas ao
longo das três execuções não geraram pedidos. Essa conclusão se restringe às
evidências e ao intervalo experimental analisados.

## 6. Comportamento do HPA

O HPA começou com uma réplica, tinha mínimo configurado de uma e máximo de
cinco, e atingiu cinco réplicas desejadas e observadas nas três execuções. Os
intervalos registrados foram os mesmos em todos os ensaios:

- Pods observados da API: mínimo 1 e máximo 5;
- réplicas desejadas pelo HPA: mínimo 1 e máximo 5; e
- réplicas atuais reportadas pelo HPA: mínimo 1 e máximo 5.

A primeira decisão acima de uma réplica ocorreu 39,346 s, 44,516 s e 39,369 s
após o início da carga. A mediana foi 39,369 s, a média 41,077 s e a amplitude
5,170 s. Portanto, os dados demonstram que o HPA atuou e que a API efetivamente
alcançou cinco Pods em todas as repetições. Eles não demonstram, isoladamente,
quanto da diferença de desempenho decorreu do HPA, pois C4 também contém rate
limiting e não houve intervenção que separasse causalmente os dois mecanismos
dentro do próprio cenário.

## 7. Latência

| Execução | Geral média / p95 / p99 (ms) | 2xx média / p95 / p99 (ms) | 429 média / p95 / p99 (ms) |
| --- | ---: | ---: | ---: |
| `c4-run-1` | 40,672 / 82,621 / 102,642 | 43,028 / 83,899 / 104,837 | 0,905 / 1,244 / 2,026 |
| `c4-run-2` | 1.043,864 / 3.645,513 / 5.226,974 | 1.106,388 / 3.669,480 / 5.234,039 | 0,876 / 1,283 / 1,626 |
| `c4-run-3` | 38,964 / 88,009 / 219,522 | 41,247 / 89,967 / 221,551 | 0,883 / 1,343 / 1,739 |

O `c4-run-2` apresenta latência geral e de respostas 2xx muito acima das outras
duas execuções. No detalhamento por estágio, seu p95 geral foi 1.501,816 ms no
primeiro estágio, 4.541,103 ms no segundo, 2.248,902 ms no terceiro e 75,830 ms
no quarto. A elevação, portanto, não se limita a uma única observação agregada.
Ao mesmo tempo, as latências das respostas 429 permaneceram próximas de 1 ms,
inclusive nesse ensaio.

Não há evidência suficiente nos consolidados para atribuir causalmente o
comportamento do `c4-run-2` ao HPA, ao rate limiting, à aplicação, ao nó ou a
algum evento externo. O resultado deve permanecer como variabilidade observada
e como ameaça à repetibilidade do desempenho, sem reclassificar a execução como
inválida ou instável.

A influência desse ensaio sobre a média é substancial. No p95 geral, a média é
1.272,048 ms, enquanto a mediana é 88,009 ms e a amplitude vai de 82,621 a
3.645,513 ms. No p99 geral, a média é 1.849,713 ms, a mediana 219,522 ms e a
amplitude 102,642–5.226,974 ms. Assim, a mediana representa melhor o
comportamento típico entre as três observações, enquanto média, amplitude e
desvio-padrão documentam a variabilidade que não pode ser ocultada.

## 8. Pedidos, fila, backlog e drenagem

Os 1.519, 1.518 e 1.518 pedidos criados foram integralmente concluídos. A taxa
de conclusão foi 100% nas três execuções, sem estados `PENDING`, `PROCESSING` ou
`FAILED` ao encerramento. O backlog máximo foi um em todos os ensaios.

A drenagem concluiu e cumpriu o objetivo nas três execuções, mas sua duração
foi assimétrica: 55 s no `c4-run-1` e 4 s nos demais. Consequentemente, a média
de 21 s é menos representativa do caso típico do que a mediana de 4 s. Os dados
não permitem afirmar que a reinicialização do gateway causou a drenagem mais
longa; apenas registram que ambos ocorreram na mesma execução.

O tempo de processamento dos pedidos também foi maior no `c4-run-2`: p95 de
637,344 ms e p99 de 804,675 ms, contra p95 de 84,534 e 97,068 ms nas outras
execuções. Ainda assim, a fila foi drenada e todos os pedidos aceitos foram
concluídos.

## 9. CPU e memória

| Execução | API CPU (cores) | API memória (MiB) | Gateway CPU (cores) | Gateway memória (MiB) |
| --- | ---: | ---: | ---: | ---: |
| `c4-run-1` | 0,470021099 | 77,390625 | 0,357650100 | 511,750000 |
| `c4-run-2` | 0,499042198 | 86,800781 | 0,041729018 | 493,816406 |
| `c4-run-3` | 0,407945532 | 84,585938 | 0,047749324 | 505,566406 |

O pico de CPU da API variou de 0,407946 a 0,499042 core, e o pico de memória de
77,390625 a 86,800781 MiB. Para o gateway, a memória permaneceu entre
493,816406 e 511,750000 MiB. O pico de CPU do gateway no `c4-run-1` foi muito
superior aos outros dois valores, o que elevou a média para 0,149043 core; a
mediana de 0,047749 core descreve melhor o valor central observado. A
coincidência com a reinicialização do gateway não basta para estabelecer uma
relação causal.

Esses números são picos amostrados no ambiente experimental, não integrais de
consumo nem estimativas de custo. Além disso, proteção de entrada, uso de
recursos e capacidade de concluir pedidos são dimensões relacionadas, mas não
intercambiáveis.

## 10. Estabilidade operacional

O conjunto apresenta duas execuções estáveis e uma instável:

- `c4-run-1`: válido, porém instável, com uma reinicialização do gateway e
  nenhuma reinicialização da API ou do worker;
- `c4-run-2`: válido e estável, sem reinicializações, apesar da latência
  elevada; e
- `c4-run-3`: válido e estável, sem reinicializações.

Não houve reinicialização da API nem do worker no C4. Isso é favorável frente
às observações de instabilidade de outros cenários, mas não autoriza afirmar
que HPA e rate limiting eliminaram o risco. A reinicialização do gateway no
primeiro ensaio demonstra risco operacional residual, e a variabilidade de
latência do segundo demonstra que estabilidade de containers não equivale a
desempenho uniforme.

## 11. Comparação descritiva com C1, C2 e C3

| Cenário | Tratamento | Amostra usada na síntese | Throughput aceito mediano (req/s) | p95 2xx mediano (ms) | p99 2xx mediano (ms) | Falhas inesperadas medianas | Observação de estabilidade |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| C1 | sem HPA e sem rate limiting | 3 válidas | 6,623394 | 4.584,267 | 30.104,240 | 364 | três execuções instáveis, uma reinicialização em cada |
| C2 | somente HPA | 3 válidas | 17,845560 | 77,999 | 206,074 | 3 | duas estáveis; uma instável com reinicialização |
| C3 | somente rate limiting | 2 válidas de 4 tentativas | 16,867553 | 71,8795 | 104,454 | 0 | duas válidas estáveis; duas tentativas inválidas e instáveis |
| C4 | HPA + rate limiting | 3 válidas | 16,867529 | 89,967 | 221,551 | 0 | duas estáveis; uma válida e instável por reinício do gateway |

### C1: controle sem os mecanismos

O C1 apresentou menor throughput aceito, latências de cauda muito superiores,
centenas de falhas inesperadas por execução, descartes e reinicializações. Uma
drenagem durou 607 s e terminou incompleta. Descritivamente, C2–C4 processaram
mais requisições e exibiram comportamento típico de latência muito melhor. A
comparação, contudo, não deve ser convertida em estimativa causal precisa: C1
atingiu condições de saturação e falha que também reduziram a carga efetivamente
processada.

### C2: HPA isolado

O C2 escalou de 1 para 5 Pods nas três execuções. Seu throughput aceito mediano
foi 17,845560 req/s, superior ao C4 porque não havia rejeições 429. A mediana de
tempo até a primeira decisão de escala foi 24,258 s no C2 e 39,369 s no C4.
Essa diferença é observacional: com somente três repetições por cenário e dois
tratamentos simultâneos no C4, não é possível atribuí-la especificamente ao
gateway ou à limitação de entrada.

O C2 também teve um ensaio instável, com 20 respostas 5xx, 57 erros de conexão
e uma reinicialização. A mediana reduz o efeito desse ensaio nas métricas de
latência, mas sua ocorrência permanece metodologicamente relevante.

### C3: rate limiting isolado

Nas duas execuções válidas do C3, throughput, percentual 429 e latência foram
próximos dos valores típicos do C4. Contudo, C3 teve apenas duas execuções
válidas na síntese; as duas tentativas posteriores foram inválidas e instáveis,
com reinicialização da API e falhas inesperadas. Comparar somente as médias dos
agregados válidos esconderia essa limitação de repetibilidade.

No C4, as três execuções foram válidas, não houve falha inesperada nem
reinicialização da API, e o HPA chegou a cinco Pods. Isso sugere, para o ambiente
testado, que a combinação preservou a proteção do Kong enquanto ofereceu
capacidade elástica à API. Ainda assim, a reinicialização do gateway impede
caracterizar a arquitetura como isenta de instabilidade.

### Leitura conjunta

Os cenários isolam configurações arquiteturais diferentes, mas não formam uma
prova causal completa. As comparações são especialmente sensíveis ao número de
execuções incluídas, às falhas observadas e ao fato de C1/C2 receberem carga
diretamente pela API, enquanto C3/C4 recebem pelo gateway. O resultado mais
defensável é descritivo: no C4, proteção de entrada e escala da API atuaram ao
mesmo tempo, e o fluxo aceito foi concluído; isso não significa que cada
mecanismo, isoladamente, explique toda diferença em relação aos demais
cenários.

## 12. Limitações e ameaças à validade

- A amostra do C4 contém somente três repetições. As estatísticas são
  descritivas e não fornecem poder para inferência, intervalos de confiança ou
  generalização ampla.
- Os testes foram locais, em um cluster Kubernetes de nó único, com janela de
  carga curta de 90 s e componentes compartilhando a mesma máquina. Contenção
  local e efeitos transitórios podem influenciar latência e recursos.
- O perfil de flash crowd, o limite de 20 req/s, o alvo de CPU de 70% e o
  intervalo de 1–5 Pods representam uma configuração específica, não todas as
  combinações possíveis.
- Os percentis agregados são estatísticas de percentis por execução. Como as
  amostras brutas não foram unidas, eles não representam o percentil de uma
  população combinada — decisão necessária para preservar a unidade
  experimental.
- O `c4-run-2` aumenta fortemente médias e desvios de latência. Sua causa não
  foi identificada pelos consolidados e não deve ser inferida.
- Métricas de CPU e memória são picos amostrados. Elas não medem consumo
  contínuo, energia, custo financeiro nem interferência detalhada entre
  processos do nó.
- O rate limiting usou uma réplica do gateway, política local e limitação por
  serviço. Os resultados não cobrem múltiplos gateways, política distribuída,
  outras janelas ou outras chaves de limitação.
- O HPA escalou apenas a API. Worker, gateway e demais componentes não tiveram
  elasticidade equivalente, e KEDA permaneceu desativado.
- Os critérios de inclusão diferem entre cenários: C3 agrega duas execuções
  válidas e preserva duas tentativas inválidas separadamente, enquanto C1, C2 e
  C4 usam três execuções válidas. Comparações diretas devem manter essa
  assimetria visível.
- Ausência de 5xx, erros de conexão ou efeitos dos 429 nestas três execuções
  não demonstra impossibilidade de ocorrência em cargas mais longas, outros
  nós, falhas de dependência ou produção.

## 13. Síntese: o que o C4 permite e não permite afirmar

### O que as evidências permitem afirmar

- Nas três execuções oficiais válidas, o Kong respondeu ao excesso com 429 e o
  HPA escalou a API de uma para cinco réplicas desejadas e observadas.
- As respostas 429 foram rejeições controladas, não falhas inesperadas, e não
  produziram pedidos no banco nas evidências consolidadas.
- Todas as respostas 2xx reconciliaram com pedidos criados e concluídos; a fila
  foi drenada e o backlog máximo foi um.
- O comportamento típico de latência, representado pela mediana, foi baixo em
  comparação descritiva com C1, mas houve variabilidade relevante por causa do
  `c4-run-2`.
- Duas execuções foram estáveis. A terceira foi válida, mas instável, devido a
  uma reinicialização do gateway.
- Proteção de entrada, capacidade elástica e conclusão do fluxo aceito foram
  observadas simultaneamente no C4.

### O que as evidências não permitem afirmar

- Não permitem atribuir a latência elevada do `c4-run-2` a um componente ou
  mecanismo específico.
- Não demonstram que HPA ou rate limiting, separados ou combinados, eliminaram
  saturação, reinicializações, variabilidade ou todos os riscos operacionais.
- Não quantificam causalmente quanto cada tratamento contribuiu para as
  diferenças entre C1, C2, C3 e C4.
- Não demonstram superioridade universal do C4 nem autorizam extrapolação
  direta para produção, clusters multinó, cargas longas ou outras políticas.
- Não transformam três repetições em evidência estatística forte. Média,
  mediana, desvio-padrão e amplitude devem permanecer juntos na interpretação.

Em síntese, o C4 fornece evidência experimental de que a proteção do gateway e
a elasticidade da API podem atuar em conjunto no ambiente estudado, mantendo o
fluxo aceito íntegro. Ao mesmo tempo, a reinicialização do gateway e a
variabilidade de latência impedem uma conclusão de risco eliminado ou de
desempenho uniformemente estável. Essa distinção entre proteção de entrada,
capacidade de processamento e estabilidade operacional deve ser preservada na
redação futura do TCC.
