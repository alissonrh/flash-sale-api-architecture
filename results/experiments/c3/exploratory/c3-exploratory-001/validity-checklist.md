# Checklist de validade

**Validade: INVALID**
**Estabilidade: STABLE**

- [x] Preflight do C3 aprovado
- [x] Node permaneceu Ready durante a coleta
- [x] Coletor terminou sem falhas
- [x] Logs, serie temporal e resumos do k6 foram copiados antes da remocao do Job
- [x] Banco, Prometheus, logs, traces e Kubernetes foram exportados
- [x] Todos os arquivos obrigatorios estao presentes
- [x] k6 terminou com codigo 0 (observado: 0)

## Estabilidade e saturacao

- Drenagem concluida: sim.
- Objetivo de drenagem de 180 segundos: atingido.
- Reinicializacoes durante a execucao (delta): 0.
- Reinicializacoes de API/worker (delta): 0.
- Desempenho ruim da aplicacao, isoladamente, nao invalida a execucao.

## Protecao de entrada do C3

- [x] Protecao funcionando: respostas 429 controladas observadas.
- [x] Invariante de classificacao das requisicoes satisfeita.
- [ ] Requisicoes 429 nao criaram pedidos nem publicaram mensagens.
- Requisicoes iniciadas: 1609.
- Aceitas (2xx): 1518.
- Protegidas/rejeitadas (429): 91 (5.656%).
- Falhas inesperadas: 0 (5xx=0, conexao=0, outros=0).
- Reinicializacoes do gateway (delta): 0.
- Respostas 429 sao o tratamento experimental esperado e nao causam, por si so, invalidade ou instabilidade.

## Motivos de invalidade

- respostas 429 podem ter produzido efeitos no banco ou RabbitMQ
