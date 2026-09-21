# Passo 14 — C4 com HPA e rate limiting

## Objetivo

Avaliar o efeito combinado do HPA da API e do rate limiting na borda, verificando
se o Kong controla o excesso de entrada enquanto o HPA amplia a capacidade da
API para processar as requisições aceitas.

## Hipótese

Sob o perfil de flash crowd congelado, o Kong deve limitar a admissão a 20
requisições por segundo e produzir respostas 429 controladas para o excesso. Ao
mesmo tempo, a CPU das requisições aceitas pode levar o HPA a aumentar a
quantidade de Pods da API. Espera-se que 429 permaneça uma resposta prevista,
enquanto 5xx, erros de conexão e outros status continuem sendo falhas
inesperadas. Esta hipótese não antecipa se haverá escala nem sua magnitude.

## Variáveis combinadas

- HPA da API ativado, como no C2.
- Rate limiting global no gateway Kong, como no C3.
- API iniciando naturalmente com uma réplica e podendo variar entre uma e cinco.
- Worker e gateway fixos em uma réplica.
- KEDA desativado.

## Configuração congelada

- HPA `api-hpa` em `autoscaling/v2`, apontando para `Deployment/api`, com mínimo
  de 1 réplica, máximo de 5 e alvo de CPU de 70% do request de 100m.
- Scale up sem janela de estabilização, política `Max`, até 4 Pods ou 400% a
  cada 15 segundos; scale down com janela de 120 segundos, política `Min` e
  remoção máxima de 1 Pod a cada 60 segundos.
- Kong `3.9.1-ubuntu` em modo DB-less, uma réplica, upstream
  `http://api:8000`, plugin `rate-limiting` no serviço `checkout-api`, limite de
  20 requisições por janela de 1 segundo, `limit_by: service`, `policy: local`,
  `hide_client_headers: false` e `fault_tolerant: false`.
- Entrada exclusiva da carga por `http://gateway:8000`.
- Uma réplica fixa do worker e uma réplica fixa do gateway; PostgreSQL,
  RabbitMQ e observabilidade iguais aos cenários anteriores.
- Perfil `startRate=1`, seguido por 20 req/s por 20s, 22 req/s por 30s, 22 req/s
  por 30s e 0 req/s por 10s; `preAllocatedVUs=100`, `maxVUs=300`, timeout de
  60s e `gracefulStop=30s`.

## Diferenças e semelhanças em relação a C1, C2 e C3

| Aspecto | C1 | C2 | C3 | C4 |
| --- | --- | --- | --- | --- |
| HPA da API | não | sim, 1–5, CPU 70% | não | sim, idêntico ao C2 |
| Rate limiting | não | não | Kong, 20 req/s | Kong, idêntico ao C3 |
| Entrada da carga | API | API | gateway | gateway |
| API | 1 fixa | inicia em 1 e escala | 1 fixa | inicia em 1 e escala |
| Worker | 1 fixa | 1 fixa | 1 fixa | 1 fixa |
| Gateway | ausente | ausente | 1 fixa | 1 fixa |
| KEDA | desativado | desativado | desativado | desativado |

O C4 mantém código, imagens da aplicação, recursos, persistência,
observabilidade, preparação de dados, carga, drenagem e cooldown comparáveis aos
cenários anteriores. Sua diferença metodológica é combinar, sem alterar, os
dois tratamentos antes isolados.

## Métricas específicas

- réplicas atuais e desejadas pelo HPA, Pods observados da API, primeira decisão
  acima de uma réplica e primeiro instante com múltiplos Pods;
- CPU e memória agregadas da API e CPU e memória do gateway;
- respostas 2xx, 429 e 5xx, erros de conexão, outros status, falhas inesperadas
  e iterações descartadas;
- throughput total, aceito e rejeitado, além de latência geral, de 2xx e de 429;
- tamanho da fila, backlog, pedidos criados e concluídos, duração e resultado da
  drenagem;
- deltas reais de reinicialização da API, do worker e do gateway, identificados
  por UID de Pod e container, sem contar criação ou remoção de Pods pelo HPA.

## Critérios de validade e estabilidade

Uma execução é válida quando o preflight confirma nó Ready, deployments
disponíveis, Metrics Server funcional, HPA com métricas e configuração idêntica
ao C2, Kong disponível com configuração idêntica ao C3 e cabeçalhos reais de
rate limiting, KEDA ausente, API inicialmente em 1/1, worker e gateway em 1/1,
dados iniciais corretos, alvo e carga congelados e evidências completas. A
classificação deve satisfazer `requisições iniciadas = 2xx + 429 + 5xx + erros
de conexão + outros status`, e somente respostas 2xx podem criar pedidos.

Respostas 429 são rejeições esperadas e não constituem falha ou instabilidade.
Respostas 5xx, erros de conexão, outros status, falhas de coleta, nó NotReady,
reinicializações reais ou drenagem incompleta são sinais inesperados. Scale up
e scale down da API não são reinicializações. A atuação do tratamento exige ao
menos uma resposta 429; sua ausência não demonstra o rate limiting combinado.

## Procedimento exploratório

Preparar o C4 com os manifests compartilhados de C2 e C3. Antes da carga,
executar o preflight conjunto, preparar os dados, aguardar 45 segundos, aguardar
métricas válidas do HPA, aguardar a API retornar naturalmente a 1/1 e validar
novamente os deployments. Só então iniciar a coleta ociosa de 60 segundos e a
carga pelo gateway. Manter a coleta durante carga, drenagem de até 600 segundos
com objetivo de 180 segundos e cooldown de 60 segundos. Exportar os logs de
todos os Pods da API por label desde o início da coleta e todas as demais
evidências, sem executar carga oficial nesta etapa.

## Critério para avançar às execuções oficiais

Avançar somente após ao menos uma execução exploratória C4 válida e estável,
com working tree e commit definidos, configurações efetivas de HPA e Kong
confirmadas, perfil congelado preservado, coleta multipod comprovada,
classificação exaustiva, efeitos no banco reconciliados, drenagem concluída e
todos os artefatos obrigatórios presentes.
