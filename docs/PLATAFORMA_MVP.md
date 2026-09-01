# Plataforma TerraFlux - MVP operacional

## Objetivo da primeira versão

Transformar o pipeline local em um fluxo rastreável de projetos, evidências,
rodadas e produtos. A plataforma não promove uma geometria porque ela foi
exibida no mapa: cada saída preserva pedido, fontes, parâmetros, hashes,
verificadores, bloqueios e nível máximo de entrega.

## O que já existe

- produtos topográficos E0 derivados e verificados;
- seis famílias E0 de sulcação;
- motor CF0 de família curva contínua;
- precursor C1 TI de alinhamentos e faixas;
- compilação de pedidos a partir de presets versionados;
- inventário de 26 pacotes de evidência e 151 parâmetros;
- relatórios e dossiê com verificação fail-closed;
- cockpit web operacional conectado a API local.

## Gaps que impedem uma plataforma multiusuário

1. API autenticada para projetos, organizações, membros e permissões.
2. Armazenamento de objetos para fontes e artefatos imutáveis.
3. PostGIS para geometrias, metadados, versões e consultas espaciais.
4. Fila durável para PDAL, GDAL, GRASS e QGIS, com cancelamento e retry.
5. Ambiente isolado por job e imagem de processamento versionada.
6. Upload multipart, retomável e com validação antes de entrar no pipeline.
7. Conversão de SHP em conjunto, LAS/LAZ/COPC, GeoTIFF/COG e tabelas.
8. Visualização vetorial por tiles; GeoJSON somente para camadas pequenas.
9. Auditoria de ações, aprovações e responsabilidade por domínio.
10. Monitoramento, custos, quotas, retenção, backup e recuperação.

## Entidades principais

| Entidade | Responsabilidade |
|---|---|
| `organization` | propriedade dos projetos, membros e cobrança |
| `project` | fazenda, escopo, CRS, jurisdição e estado atual |
| `dataset_asset` | arquivo original, assinatura, tipo, cobertura e QA |
| `evidence_package` | suficiência, validade, responsável e parâmetros resolvidos |
| `preset_selection` | composição de defaults e valores próprios |
| `generation_request` | pedido imutável usado por todos os motores |
| `run` | execução, imagem, logs, progresso, custo e resultado |
| `artifact` | mapa, raster, vetor, PDF, manifesto e checksum |
| `review` | decisão humana, comentário, gate e assinatura |

## Estados de uma execução

`DRAFT -> VALIDATING -> QUEUED -> RUNNING -> VERIFYING -> SUCCEEDED`

Saídas alternativas: `BLOCKED`, `FAILED`, `CANCELLED` e `SUPERSEDED`.
`SUCCEEDED` significa que o estágio passou no seu próprio contrato; não
significa projeto executivo ou autorização de guiamento.

## API inicial

| Método | Rota | Uso |
|---|---|---|
| `POST` | `/projects` | criar projeto e jurisdição |
| `POST` | `/projects/{id}/assets` | iniciar upload validado |
| `GET` | `/projects/{id}/readiness` | evidências, parâmetros e bloqueios |
| `POST` | `/projects/{id}/preset-resolutions` | resolver seleção de presets |
| `POST` | `/projects/{id}/requests` | compilar pedido imutável |
| `POST` | `/requests/{id}/runs` | executar E0, CF0 ou precursor permitido |
| `GET` | `/runs/{id}` | progresso, logs e fronteira de entrega |
| `GET` | `/runs/{id}/artifacts` | produtos e checksums |
| `POST` | `/artifacts/{id}/reviews` | decisão e aprovação por domínio |

## Ordem de implementação

### P0 - plataforma utilizável

- projetos e usuários;
- upload e catálogo de assets;
- prontidão e parâmetros;
- compilação de pedido;
- jobs E0/CF0/C1 E0;
- mapa, relatórios e download de artefatos;
- logs, hashes e bloqueios visíveis.

### P1 - decisão operacional

- inventário de barreiras por operação;
- superfícies, portais, cabeceiras e frota;
- estado de caminho operacional;
- comparação de tiros e continuidade entre talhões;
- POA estático integrado aos segmentos aprovados.

### P2 - conservação dimensionada

- incerteza vertical;
- PCE e PCX reproduzíveis;
- chuva-excesso, hidrogramas e receptores;
- seções, superfície proposta e volumes;
- C1 TI/TD dimensionado;
- C2 base larga/passante e C3 ESD.

### P3 - implantação

- simulador articulado da frota;
- operação e logística por eventos;
- piloto e aceite de campo;
- as built, manutenção e ciclo de vida;
- exportação para controladores somente após aprovação.

## Decisões técnicas iniciais

- backend Python, preservando os motores existentes como pacotes de domínio;
- API separada dos workers geoespaciais;
- PostgreSQL/PostGIS para estado transacional e espacial;
- armazenamento de objetos compatível com S3;
- fila durável com idempotência por hash do pedido;
- COG para rasters, COPC para nuvem e GeoPackage como pacote técnico;
- manifests JSON continuam sendo o contrato entre motor e plataforma;
- nenhum job pode remover bloqueios declarados pelo verificador.
