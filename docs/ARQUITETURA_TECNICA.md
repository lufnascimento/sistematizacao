# Arquitetura Técnica

## 1. Estratégia

Construir um **monólito modular com workers geoespaciais**, não uma malha prematura de microsserviços. Separar desde o início API transacional, armazenamento de objetos e jobs pesados.

```text
Browser
  React + TypeScript
  MapLibre 2D + Giro3D
          |
      API FastAPI
       /    |    \
PostGIS   S3/MinIO   Orquestrador
metadados COG/COPC   filas e estados
vetores   originais       |
                    Workers isolados
             PDAL/GDAL/PROJ/GRASS
                     |
             resultados versionados
```

## 2. Stack recomendada

| Camada | Escolha | Motivo |
|---|---|---|
| Web | React + TypeScript | Editor rico, componentes e ecossistema GIS |
| Mapa 2D | MapLibre GL JS | Vetores, raster, hillshade e terreno WebGL |
| Nuvem/3D | Giro3D | COG, elevação, COPC, LAS/LAZ, perfis e 3D Tiles |
| API | FastAPI + Pydantic | Python próximo dos motores científicos e contratos tipados |
| Banco | PostgreSQL + PostGIS | Topologia, spatial SQL, transações e RLS |
| Objetos | S3; MinIO on-premises | Arquivos grandes, versionamento e upload multipart |
| Filas | SQS + Step Functions/Temporal | Jobs longos, retomada, timeout e compensação |
| Compute | AWS Batch/ECS; Kubernetes depois | CPU/RAM elásticos e isolamento de ferramentas nativas |
| Geo | PDAL, GDAL, PROJ, GRASS, Rasterio, GeoPandas, Shapely | Ferramentas maduras e reproduzíveis |
| Otimização | Geometria própria + OR-Tools | Separar forma contínua das decisões discretas |
| Tiles | TiTiler + CDN, MVT/PostGIS | Leitura parcial de COG e vetores grandes |
| Identidade | OIDC/Cognito/Auth0/Keycloak | Não construir autenticação própria |
| Observabilidade | OpenTelemetry + métricas/logs/traces | Custo e falha por estágio/projeto |

MapLibre suporta fonte raster DEM e terreno 3D. [Documentação MapLibre](https://maplibre.org/maplibre-gl-js/docs/examples/3d-terrain/)

## 3. Armazenamento canônico

- **Originais:** imutáveis, exatamente como enviados.
- **Nuvens:** COPC para acesso parcial HTTP.
- **Rasters:** COG com blocos, compressão, máscara e overviews.
- **Vetores de trabalho:** PostGIS.
- **Tabelas/derivados extensos:** GeoParquet.
- **Catálogo e linhagem:** STAC + tabelas de domínio.
- **Entregáveis:** GeoPackage, DXF, SHP, KML/KMZ, CSV e PDF.

Não guardar nuvem completa no PostgreSQL. Não usar PostGIS Raster como depósito principal de ortomosaicos/MDTs grandes.

COPC organiza LAZ em octree e permite consultas parciais. COG organiza TIFF para leitura eficiente por faixas HTTP. [COPC](https://copc.io/), [GDAL COG](https://gdal.org/en/stable/drivers/raster/cog.html)

## 4. Modelo de domínio

Entidades principais:

```text
organization
  farm
    project
      parcel
      dataset
        asset
        qc_report
      terrain_model
      generation_request / resolved_parameter_manifest
      soil_zone / rain_source / fleet_profile
      interference_catalog
        interference_feature / exclusion_area / work_break
      constraint / structure_existing
      analysis_run
        finding
      scenario
        structure_proposed
        row_plan
        operational_graph / hydraulic_graph / constraint_graph
        harvest_plan / load_event / swap_window
        poa_plan / poa_site / transfer_bay
        logistics_graph / route / logistics_simulation
        traffic_intensity_surface
        metric
        violation
      review / field_visit / approval
      export / audit_event
```

Regras:

- `dataset` e `asset` são imutáveis e têm SHA-256.
- `terrain_model` referencia original, QA, correções e parâmetros.
- `analysis_run` referencia versões exatas de dados, imagem do worker e pacote de regras.
- `scenario` nunca sobrescreve outro; cria revisão.
- toda geração referencia o pedido e o manifesto resolvido de parâmetros;
- interferências alteram de forma independente plantio, colheita, trânsito, obra, hidráulica e POA;
- `power_line_axis` é barreira absoluta na V1 e não produz portal automático;
- POA é polígono operacional capacitado, não ponto cartográfico.
- `approval` assina hash do cenário e é revogada por qualquer dependência alterada.
- Todas as tabelas de negócio carregam `organization_id` e política RLS.

## 5. API inicial

```text
POST   /projects
GET    /projects/{id}/readiness
POST   /projects/{id}/uploads:init
POST   /uploads/{id}:complete
GET    /datasets/{id}/quality
POST   /datasets/{id}:approve
POST   /terrain-models
POST   /projects/{id}/generation-requests
POST   /projects/{id}/interference-catalogs
POST   /analyses/hydrology
GET    /jobs/{id}
POST   /scenarios:generate
POST   /scenarios/{id}:optimize-poa-logistics
POST   /scenarios/{id}:simulate-harvest
PATCH  /scenarios/{id}/features/{feature_id}
POST   /scenarios/{id}:validate
POST   /scenarios/{id}:submit-review
POST   /scenarios/{id}:approve
POST   /scenarios/{id}/exports
GET    /projects/{id}/audit
```

Upload deve ir do navegador diretamente ao object storage por URLs assinadas e multipart. A API recebe metadados e conclui o upload, sem retransmitir gigabytes. A AWS recomenda multipart para objetos a partir de 100 MB. [AWS S3 multipart](https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html)

Atualização de progresso por Server-Sent Events ou WebSocket. Jobs devem ser idempotentes e identificados por hash de input + parâmetros + versão.

## 6. Estados de job

```text
queued
  → validating
  → normalizing
  → processing
  → quality_review_required | succeeded
  → failed_retryable | failed_terminal | cancelled
```

Cada estágio registra:

- Início/fim e tentativas.
- Container/digest e versões das bibliotecas.
- Parâmetros normalizados.
- CPU, memória, scratch e bytes.
- Logs estruturados.
- Outputs e checksums.
- QA e advertências.
- Motivo de falha acionável.

## 7. Pipeline de ingestão

```text
 upload
 → inspeção de segurança
 → leitura de metadados
 → validação de CRS/datum/unidade
 → classificação da camada e declaração de completude
 → teste de sobreposição e cobertura
 → conversão COPC/COG
 → geração de previews/tiles
 → relatório de qualidade
 → aprovação ou ação necessária
```

Processar GDAL/PDAL em contêiner sem rede, com diretório temporário exclusivo, quotas e lista de opções permitidas. Nunca montar comando shell por concatenação do nome enviado.

## 8. Pipeline da nuvem e MDT

1. Ler LAS/LAZ/COPC e conferir bounds/classes.
2. Remover duplicatas e outliers com parâmetros registrados.
3. Reprojetar horizontal e verticalmente.
4. Recortar blocos com overlap para evitar bordas de classificação.
5. Classificar solo por SMRF e método alternativo/CSF.
6. Comparar as classificações e gerar mapa de confiança.
7. Permitir correção manual de classes e breaklines.
8. Interpolar por TIN/IDW conforme densidade.
9. Guardar distância até ponto observado e máscara de extrapolação.
10. Comparar com checkpoints e produzir resíduos.
11. Criar hillshade, declividade e perfis de QA.
12. Publicar COPC/COG e relatório.

PDAL fornece pipelines JSON reproduzíveis e filtro SMRF para classificação de terreno. [PDAL: identificação de solo](https://pdal.org/en/stable/workshop/manipulation/ground/ground.html)

Preservar três superfícies:

- `dtm_original`: derivado sem correção hidráulica.
- `dtm_qc`: ruídos/correções aprovados.
- `dtm_hydro_conditioned`: bueiros, canais, barreiras e conectividade.

## 9. Motor hídrico

### Diagnóstico rápido

- Declividade, aspecto e curvaturas.
- MFD como padrão de distribuição em encosta.
- D8 para redes concentradas.
- D∞ como sensibilidade.
- Acumulação, direção e microbacias.
- TWI/TCI, SPI e comprimento hidráulico.
- Depressões, profundidade e volume.
- Fatores `LS` e `S` da RUSLE.
- Entradas/saídas e fluxo cruzando bordas.

O `r.watershed` do GRASS calcula acumulação, drenagem, bacias, índices topográficos e fatores RUSLE. [GRASS r.watershed](https://grass.osgeo.org/grass-stable/manuals/r.watershed.html)

### Chuva de projeto no MVP

Usar SIMWE/`r.sim.water` para chuva excedente, infiltração, Manning e escoamento superficial 2D. Saídas: profundidade, fluxo, velocidade, tempo e balanço. [GRASS r.sim.water](https://grass.osgeo.org/grass-stable/manuals/r.sim.water.html)

Para cálculo simplificado e verificações:

- CN ou Green-Ampt para perdas/infiltração.
- Método Racional apenas no domínio em que suas hipóteses forem válidas.
- Manning para canais e seções.
- Reservatório nível-volume-descarga para retenções.
- Propagação e falha entre estruturas conectadas.

### Validação detalhada

HEC-RAS 2D pode validar hotspots e cenários complexos com Rain-on-Grid e camadas de infiltração. Não deve ser motor de cada interação do editor, pois preparação de malha e tempo computacional são maiores. [USACE HEC-RAS 2D](https://www.hec.usace.army.mil/software/hec-ras/documentation.aspx)

## 10. Motor de erosão

- RUSLE espacial para perda média anual e comparação de manejo.
- SIMWE sediment para cenário comparativo.
- WEPP somente depois de obter base regional de solo, clima e manejo.
- Erosão concentrada/canal separada da erosão laminar.
- Intervalo de sensibilidade para `R`, `K`, `C` e `P`.

O output nunca deve chamar RUSLE de “previsão da próxima chuva”.

## 11. Motor geométrico

### Terraços/canais

```text
rampas + vazão + solo + rule pack
 → alinhamentos candidatos
 → espaçamento inicial
 → perfil e seção
 → conexão com saída
 → corte/aterro e mecanização
 → MDT proposto
 → nova simulação
 → validações
```

### Sulcação

1. Resolver o manifesto de parâmetros e recusar fatos/regras obrigatórios ausentes.
2. Subtrair APPs, estruturas, estradas, cabeceiras e buffers de interferências.
3. Dividir área útil em blocos operacionais.
4. Gerar linhas-mestras por caminhos de custo no terreno.
5. Restringir inclinação, curvatura e aproximação ao contorno.
6. Suavizar com splines/clotoides dentro da tolerância.
7. Propagar offsets no espaçamento agronômico.
8. Resolver clipping, encontros e fragmentos.
9. Quebrar `worked_segment`, `hydraulic_reach` e rota nas barreiras.
10. Criar cabeceiras e sequência operacional.
11. Amostrar perfil e executar verificadores por linha.
12. Modelar sulcos/trilhas como caminhos preferenciais e recalcular a água.

OR-Tools resolve seleção de candidatos, blocos e decisões discretas. Geometria contínua deve permanecer em algoritmo próprio; um solver discreto não deve desenhar a curva. [OR-Tools CP-SAT](https://developers.google.com/optimization/cp/cp_solver)

## 12. Otimização multicritério

Primeiro eliminar toda alternativa que viole restrições duras. Depois calcular frente de Pareto usando:

- Pico e velocidade.
- Perda potencial de solo.
- Comprimento hidráulico.
- Área plantável.
- Comprimento médio de linhas.
- Manobras, bicos e linhas curtas.
- Distância operacional.
- Quilômetros de transbordo vazio/carregado e espera da colhedora.
- POAs, capacidade de baias, filas e regularidade de entrega.
- Área trafegada, repetição de passadas e sobreposição com cultura.
- Corte/aterro.
- Custo de implantação e manutenção.
- Incerteza e sensibilidade.

Pesos do usuário servem para ordenar alternativas válidas, nunca para compensar saída hidráulica insegura.

### POA e logística CTT

O pipeline acoplado é:

```text
row_plan + yield_surface
 -> mass_along_shot + load_events + swap_windows
 -> directed_road_graph(empty/loaded)
 -> capacitated_poa_location_and_assignment
 -> vehicle_routing_and_dispatch
 -> discrete_event_simulation
 -> traffic_and_queue_metrics
 -> feedback_to_rows_exits_and_carriers
```

Localização capacitada e decisões discretas podem usar OR-Tools; rotas seguem o grafo dirigível com largura, piso, declividade, carga, sazonalidade e barreiras; uma simulação de eventos discretos mede variabilidade de produtividade, tempos, falhas, atrasos e bloqueios. A superfície proposta e os mapas de água/tráfego são recalculados depois de qualquer mudança geométrica.

Na V1, nenhuma aresta do grafo, POA, baia, fila ou `swap_window` cruza o buffer de `power_line_axis`. Sem camada revisada ou `DECLARED_NONE`, o job pode produzir triagem geométrica, mas não plano operacional.

## 13. Visualização e edição

### Desktop

- Mapa 2D como editor principal.
- COG via TiTiler/CDN.
- GeoJSON apenas para camadas pequenas; MVT para grandes.
- Nuvem COPC com nível de detalhe.
- Terreno 3D e ortomosaico drapeado.
- Swipe antes/depois.
- Perfis ligados à seleção.
- Timeline de evento baseada em cálculo real.
- Mapa de incerteza acessível em todas as análises.
- Erros e regras atualizados após edição.

### Mobile

Não reproduzir o editor desktop. Construir fluxo específico de campo:

- Projetos/pacotes offline.
- GPS, ponto, linha, foto e formulário.
- Navegação até achado/estrutura.
- Confirmação de bueiro, saída, erosão e solo.
- Pendências e sincronização.
- Aprovação simples, sem edição geométrica complexa.

## 14. Performance e dimensionamento

Uma área de 1.000 ha contém aproximadamente:

| Resolução | Células |
|---|---:|
| 1,00 m | 10 milhões |
| 0,50 m | 40 milhões |
| 0,25 m | 160 milhões |

Estratégia:

- Prévia a 1 m.
- Diagnóstico padrão a 0,5 m quando a precisão justificar.
- 0,25 m somente em hotspots ou áreas menores.
- Hidrologia por microbacia completa; não processar tiles independentes.
- Classificação de nuvem em tiles com overlap.
- Cache por hash de cada estágio.
- Scratch NVMe de 2 a 3 vezes o maior artefato.
- Modo em disco/TauDEM MPI para áreas grandes.
- Limites por plano: bytes, pontos, células e tempo.

Exemplo de ordem de grandeza para 500 ha a 5 cm: cerca de 2 bilhões de pixels RGB antes de compressão. A arquitetura precisa assumir gigabytes por projeto e uploads interrompíveis.

## 15. Segurança e privacidade

- Tenant por organização e projeto.
- Postgres RLS e IAM mínimo.
- Objetos privados, TLS e criptografia KMS.
- URLs assinadas de curta duração.
- Auditoria de acesso, download, edição e aprovação.
- Contêineres sem egress de internet.
- Antimalware e proteção contra arquivos hostis.
- TiTiler limitado a URLs internas para evitar SSRF.
- Segredos em secret manager.
- Backups com teste de restauração.
- Política de retenção, exportação e exclusão.
- Contrato sobre propriedade dos dados e uso para treinamento.
- Adequação à LGPD quando limites e dados forem vinculados a pessoas.

## 16. Observabilidade e confiabilidade

Painéis mínimos:

- Uploads e validações por resultado.
- Jobs por estágio, duração, retry e erro.
- Pontos/células processados.
- CPU, memória, scratch e custo por projeto.
- Cache hit.
- Tile latency e erros do mapa.
- Exportações e aprovações.

Testes:

- Unitários de fórmulas e regras.
- Property-based para geometria/topologia.
- Rasters sintéticos: plano, cone, vale, depressão e bueiro.
- Golden datasets com outputs versionados.
- Regressão numérica com tolerâncias.
- Integração de upload até exportação.
- Segurança de formatos e isolamento.
- Compatibilidade real com controladores agrícolas.

## 17. Make versus buy

### Construir

- Contrato e gates de confiança.
- Rule packs agronômicos regionais.
- Condicionamento água-solo-cana.
- Geradores de estruturas e sulcação.
- Verificadores e Pareto.
- Aprovação, memória e as built.
- Adaptadores agrícolas prioritários.

### Reutilizar/comprar

- GDAL, PDAL, PROJ, GRASS e PostGIS.
- COG, COPC, STAC e GeoParquet.
- MapLibre, Giro3D e TiTiler.
- Identidade, billing e observabilidade.
- HEC-RAS para validação especializada.
- Fotogrametria por produto existente.

Não construir decodificador LAS, tiler raster, autenticação ou solver hidráulico do zero.

## 18. Ambientes e entrega

- Infraestrutura como código.
- Ambientes dev, staging e produção isolados.
- Imagens Docker fixadas por digest.
- Migrações versionadas.
- Feature flags para motores novos.
- Rule packs assinados/versionados por região.
- Rollback da aplicação sem apagar resultados anteriores.
- Dataset sintético pequeno em CI; golden datasets grandes em pipeline noturno.
