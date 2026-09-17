# Passo 10 - Preparacao do C2 com HPA

**Status: infraestrutura preparada; nenhum teste foi executado nesta tarefa.**

## Objetivo experimental

O C2 mede o efeito do escalonamento horizontal da API sob o mesmo flash crowd
do C1. A unica variavel independente alterada e a ativacao do HPA da API
(controlador que ajusta automaticamente a quantidade de Pods). O C1 continua
sendo o controle com uma replica fixa da API.

## Diferenca entre C1 e C2

| Configuracao | C1 | C2 |
| --- | --- | --- |
| Replicas da API | 1 fixa | HPA de 1 a 5 |
| Alvo de CPU da API | nao se aplica | 70% do request |
| Replicas do worker | 1 fixa | 1 fixa |
| Rate limiting | desligado | desligado |
| Ingress, Kong e KEDA | ausentes | ausentes |

O C2 usa somente `k8s/api-hpa.yaml` como manifesto especifico do cenario.

## Variaveis mantidas fixas

- imagem e codigo da API e do worker;
- requests e limits de CPU e memoria;
- uma replica do worker;
- banco PostgreSQL, RabbitMQ e observabilidade;
- rate limiting desligado e ausencia de Ingress, Kong e KEDA;
- perfil de carga `1 -> 20 -> 22 -> 22 -> 0 req/s`;
- duracoes `20s -> 30s -> 30s -> 10s` e demais parametros do k6;
- intervalo de coleta de 5 segundos, preparacao dos dados, drenagem e cooldown.

## Configuracao do HPA

O minimo de uma replica preserva a disponibilidade da API em repouso. O maximo
de cinco replicas limita o consumo do ambiente local e, ao mesmo tempo, permite
observar uma resposta relevante ao flash crowd. O alvo de 70% oferece margem
para escalar antes da saturacao sustentada.

Os 70% sao calculados sobre o **CPU request de 100m** (CPU reservada usada como
referencia pelo HPA), e nao sobre o **CPU limit de 500m** (teto de CPU permitido
ao container). Portanto, o alvo corresponde a uma media de aproximadamente
70m de CPU por Pod.

O Metrics Server (componente que fornece uso recente de CPU e memoria ao
Kubernetes) precisa estar disponivel para o HPA calcular replicas. O scale up
(aumento de replicas) nao possui janela de estabilizacao e permite adicionar
ate quatro Pods em 15 segundos, de modo que a API possa ir de uma a cinco
replicas rapidamente. O scale down (reducao de replicas) possui janela de
estabilizacao de 120 segundos e remove no maximo um Pod por minuto. A
stabilization window (periodo que conserva recomendacoes anteriores antes de
reduzir) evita flapping (criacao e remocao repetida de Pods) e ajuda a manter os
Pods disponiveis ate a exportacao das evidencias.

## Comandos futuros

Bootstrap do C2:

```bash
bash scripts/bootstrap-k8s.sh --scenario c2
```

Exemplo de execucao exploratoria com a carga oficial congelada:

```bash
bash scripts/run-k8s-experiment.sh \
  --scenario c2 \
  --type exploratory \
  --id c2-exploratory-001 \
  --start-rate 1 \
  --stage-1-rate 20 --stage-1-duration 20s \
  --stage-2-rate 22 --stage-2-duration 30s \
  --stage-3-rate 22 --stage-3-duration 30s \
  --stage-4-rate 0 --stage-4-duration 10s
```

Esses comandos sao apenas orientacao para uma etapa futura. Nenhum bootstrap,
teste de carga, seed ou reset de banco foi executado durante esta preparacao.

## Criterios antes das execucoes oficiais

- branch e commit oficiais definidos, com working tree limpo;
- Metrics Server disponivel e HPA com metrica atual de CPU;
- somente o HPA `api-hpa`, apontando para `Deployment/api`, com minimo 1,
  maximo 5 e alvo de CPU 70%;
- API novamente no baseline 1/1 antes de cada execucao;
- worker exatamente em 1/1;
- Ingress, Kong, rate limiting e KEDA ausentes;
- fila vazia, um consumidor e banco no estado inicial esperado;
- perfil `1 -> 20 -> 22 -> 22 -> 0 req/s` conferido sem alteracoes;
- coleta gerando `hpa-samples.csv` e os demais artefatos obrigatorios;
- ID iniciado por `c2-` e diretorio de resultados ainda inexistente;
- uma execucao exploratoria valida concluida antes das repeticoes oficiais.
