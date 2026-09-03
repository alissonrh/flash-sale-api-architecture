# Checklist de validade

**Resultado: INVALID**

- [x] Preflight do C1 aprovado
- [x] Node permaneceu Ready durante a coleta
- [ ] Nenhum Pod reiniciou durante a coleta
- [x] Coletor terminou sem falhas
- [x] Logs e summary do k6 foram copiados antes da remocao do Job
- [x] Fila e pedidos ativos drenaram dentro da janela maxima
- [x] Banco, Prometheus, logs, traces e Kubernetes foram exportados
- [x] Todos os arquivos obrigatorios estao presentes
- [x] k6 terminou com codigo 0 (observado: 0)

## Observacoes

- Objetivo de drenagem de 180 segundos: atingido.
- Desempenho ruim da aplicacao, isoladamente, nao invalida a execucao.

## Motivos de invalidade

- ao menos um Pod reiniciou durante a coleta
