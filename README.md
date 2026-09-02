# TerraFlux

Planejamento conservacionista e sistematização de canaviais orientados pelo caminho da água.

## Estado atual

O repositório contém uma **aplicação local operacional** para cadastrar projetos,
receber arquivos do cliente, configurar produtos, congelar pedidos imutáveis,
executar topografia e cenários E0/CF0 em fila, comparar resultados e baixar um
dossiê PDF rastreável. O pipeline usa PDAL, GDAL e GRASS instalados com QGIS.
Ainda não é um serviço de produção e
os resultados atuais são triagem topográfica/geometrica: não constituem projeto
executivo, aprovação hidráulica ou autorização de guiamento.

O fluxo operacional atual está documentado em
[platform/README.md](./platform/README.md) e é servido por
`python platform/run.py`. A antiga demonstração S0-S6 e seus mapas de um caso
real permanecem somente no workspace local, fora do repositório público.

Os PDFs recebidos são apenas exemplos visuais das famílias de entrega. Eles não fornecem parâmetros, verdade-terreno ou critérios de aceitação, e não são alvo de reprodução.

## Organização do repositório

- `platform/`: aplicação operacional, API, worker, frontend e testes próprios.
- `scripts/`: motores GIS, validadores e geradores de relatórios.
- `tests/`: testes dos motores e contratos geoespaciais.
- `config/` e `schemas/`: presets, catálogos e contratos de dados.
- `docs/`: pesquisa agronômica, arquitetura, decisões e roadmap.
- `dataset/`: documentação dos insumos locais; uploads e derivados não são
  publicados no Git.

Veja [docs/REPOSITORIO.md](./docs/REPOSITORIO.md) para a política de dados,
artefatos e verificação antes de commits.

Para executar dados do cliente, use a aplicação da pasta `platform`.

## Veredito

O produto é tecnicamente viável como plataforma de diagnóstico, anteprojeto e projeto assistido por profissional. Não é confiável prometer projeto executivo automático apenas com o polígono do talhão e um ortomosaico.

- Ortomosaico sem elevação: cadastro e fotointerpretação.
- MDT validado ou nuvem classificada: diagnóstico do relevo e da água.
- Solo, infiltração, chuva, contexto da microbacia, estruturas e máquinas: geração e comparação de alternativas.
- Vistoria, memória de cálculo e responsável habilitado: liberação para execução.

## Documentação

- [Plataforma MVP](./docs/PLATAFORMA_MVP.md): arquitetura inicial, entidades, API, estados de jobs e ordem de implementação para transformar o pipeline local em sistema multiusuário.
- [Produto analítico de sulcação](./docs/PRODUTO_ANALITICO_DE_SULCACAO.md): carteira obrigatória de curva embutida, base larga/passante e ESD, curvas híbridas, conexão multi-talhão/multifazenda, gates, métricas e teste E0.
- [Presets conservacionistas](./config/cenarios_conservacionistas.json): contrato legível por máquina dos macrocenários, modos `OC0`–`OC3`, barreiras e cenários de POA `P0`–`P3`.
- [Catálogo de parâmetros](./config/catalogo_parametros_projeto.json): 151 parâmetros, dos quais 113 são `safety_critical`, cobrindo fatos do usuário, cálculos, regras, preferências, defaults permitidos e gates.
- [Guia de aquisição de insumos](./docs/GUIA_AQUISICAO_INSUMOS_E_PARAMETROS.md): o que existe, o que falta, como obter, validar, atualizar e bloquear cada pacote.
- [Biblioteca de presets e modelos reais](./docs/BIBLIOTECA_PRESETS_MODELOS_REAIS_CANA.md): 48 fontes e 65 modelos para os 139 parâmetros configuráveis, com defaults E0, packs regionais, equipamentos OEM, valores próprios e bloqueio explícito do ESD sem overlay profissional.
- [Catálogo de modelos reais](./config/catalogo_modelos_reais_cana.json): métodos, censos, fontes oficiais, protocolos, layouts e fichas nominais de equipamentos, todos com proveniência e teto de liberação.
- [Catálogo de presets do sistema](./config/catalogo_presets_sistema.json): liga os 26 pacotes e os 10 parâmetros independentes a defaults e alternativas selecionáveis.
- [Exemplo de seleção de presets](./config/exemplo_selecao_presets_sistema.json): demonstra composição por jurisdição, modelos OEM e substituição por valor próprio.
- [Resolução E0 anonimizada](./config/exemplo_resolucao_presets_e0.json): resultado autocontido usado pelo pacote público de compilação e por seus testes de linhagem.
- [Catálogo de aquisição](./config/catalogo_aquisicao_insumos.json): 26 contratos de evidência com fontes, campos, unidades, protocolos, QA e validade.
- [Catálogo de capacidades do motor](./config/catalogo_capacidades_motor.json): separa falta de dado de solver ainda não implementado.
- [Motor PCE0 e PCX0](./docs/MOTOR_PCE0_PCX0.md): núcleos reproduzíveis de triagem RUSLE e balanço de massa de evento, com limites fail-closed e sem alegação de dimensionamento hidráulico.
- [Motor PCX1 de chuva-excesso](./docs/MOTOR_PCX1_CHUVA_EXCESSO.md): método NRCS-CN incremental, jornada do usuário, produtos JSON/CSV e integração limitada com a plataforma.
- [Hidrograma preliminar](./docs/MOTOR_HIDROGRAMA_PRELIMINAR.md): vazao no tempo, pico preliminar, grafico, CSV/JSON e balanco de volume, ainda sem propagacao ou dimensionamento hidraulico.
- [Propagacao preliminar na rede](./docs/PROPAGACAO_PRELIMINAR_NA_REDE.md): grafo aciclico, confluencias, atraso por trecho e balanco nas saidas, ainda sem atenuacao ou capacidade hidraulica.
- [Verificacao preliminar de capacidade](./docs/VERIFICACAO_PRELIMINAR_CAPACIDADE.md): secao trapezoidal, Manning, profundidade normal, velocidade e tensao diagnostica por trecho.
- [Perfil preliminar da lamina](./docs/PERFIL_PRELIMINAR_LAMINA.md): passo padrao permanente e subcritico ao longo de trechos prismaticos declarados.
- [Modelos de referencia para estabilidade hidraulica](./config/catalogo_limites_estabilidade_hidraulica.json): opcoes selecionaveis e valores proprios, sempre sem aprovacao automatica.
- [Schema do pedido de geração](./schemas/project-generation-request.schema.json): contrato dos insumos, parâmetros, interferências, objetivos e overrides de cada rodada.
- [Schema de superfícies operacionais](./schemas/operational-surface-layer.schema.json): separa locais que apenas suportam término de trabalho daqueles realmente aprovados para manobra, por operação e perfil de frota.
- [Pedido E0 de exemplo](./config/exemplo_pedido_e0_dataset_atual.json): instância pública anonimizada, válida e deliberadamente limitada a triagem.
- [Schema de cenário](./schemas/conservation-scenario.schema.json): contrato técnico 2.1 com zonas, sulcação/terraço/ESD, grupos de guia, portais, interferências, POA e elegibilidade.
- [Schema do manifesto estático de POA](./schemas/poa-static-stage-manifest.schema.json): contrato de `scripts/optimize_static_poa.py` — estágio a jusante do pedido central, nunca traduzido automaticamente a partir dele.
- [Auditoria de gaps, parâmetros e interferências](./docs/AUDITORIA_DE_GAPS_E_PARAMETROS.md): lacunas P0–P2, responsabilidades, invalidação, barreira elétrica e critérios de pronto.
- [POA e logística de colheita](./docs/POA_E_LOGISTICA_DE_COLHEITA.md): massa por tiro, pátios, rotas, transbordos, filas, tráfego e realimentação da sulcação.
- [Plano mestre](./docs/PLANO_MESTRE.md): visão completa, escopo, regras, módulos e decisões.
- [Contrato de dados](./docs/CONTRATO_DE_DADOS.md): arquivos aceitos, levantamento, metadados e gates de qualidade.
- [Arquitetura técnica](./docs/ARQUITETURA_TECNICA.md): stack, pipeline, motores, dados, APIs, segurança e performance.
- [Roadmap e validação](./docs/ROADMAP_VALIDACAO.md): fases, equipe, orçamento, pilotos, testes e critérios de go/no-go.
- [Métodos conservacionistas regionais](./docs/METODOS_CONSERVACIONISTAS_REGIONAIS.md): TI, TD, base larga/passante, embutido, CEV, ESD/ST, regras e responsabilidade.
- [Lógica do ESD e do canal escoadouro](./docs/LOGICA_ESD_E_CANAL_ESCOADOURO.md): definição, componentes, cadeia da água, geração de sulcos, receptores, simulação, insumos e validação.
- [Motor de cenários e sulcação](./docs/MOTOR_DE_CENARIOS_E_SULCACAO.md): geração, otimização, taxonomia fatorial, tiros entre talhões e benchmark AgroCAD.
- [Catálogo de produtos](./docs/CATALOGO_PRODUTOS.md): produtos L0–L3, insumos, gates e distinção entre cota estática, chuva-vazão e inundação hidráulica.

## Dados locais

Uploads, coordenadas, identificadores de talhão, diagnósticos da fazenda e
produtos derivados permanecem em `dataset/` e `platform_runtime/`, ambos fora do
histórico Git. O repositório público contém contratos e exemplos anonimizados;
eles não representam uma propriedade real nem autorizam guiamento ou execução.

Reprocesse os derivados, na raiz do projeto, com:

```powershell
& 'C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat' '.\scripts\audit_dataset.py'
& 'C:\Program Files\QGIS 3.32.1\bin\grass83.bat' --tmp-location '.\dataset\DEM.tif' --exec '.\scripts\run_preliminary_hydrology.bat'
& 'C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat' '.\scripts\audit_dataset.py'
& 'C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat' '.\scripts\generate_source_products.py'
& 'C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat' '.\scripts\generate_sulcation_scenarios.py' --request '.\config\exemplo_pedido_e0_dataset_atual.json'
& 'C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat' '.\scripts\verify_sulcation_scenarios.py'
python '.\scripts\validate_conservation_portfolio.py'
python '.\scripts\validate_project_inputs.py' --request '.\config\exemplo_pedido_e0_dataset_atual.json'
python '.\scripts\audit_project_readiness.py'
python '.\scripts\audit_system_presets.py'
python '.\scripts\resolve_project_presets.py' --selection '.\config\exemplo_selecao_presets_sistema.json'
python '.\scripts\validate_operational_surfaces.py'
& 'C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat' -m unittest -v tests.test_sulcation_phase tests.test_operational_continuity tests.test_operational_surface_contract tests.test_terrain_coverage tests.test_optimize_static_poa
```

## Princípio do produto

> Água e barreiras primeiro; operação e logística depois.

As restrições ambientais/fundiárias/físicas, a microbacia, as entradas e saídas de água e as estruturas conservacionistas são definidas antes de carreadores, cabeceiras, linhas de sulcação, rotas e POAs.

## Próximo marco

O motor CF0 1.2 resolve a família contínua geométrica, mas permanece `HYDRAULIC_UNCONFIRMED`. O estágio `C1_ALIGNMENT_SCREENING` gera sensibilidade TI limitada; PCE0/PCX0 cobrem triagem RUSLE e balanço volumétrico; e a cadeia de chuva, hidrograma, propagação, capacidade e perfil calcula estados da seção, limites declarados, borda livre, transbordamento e lâmina permanente subcrítica ao longo dos trechos. Ainda não há regime misto, estruturas, simulação espacial do caminho excedente, validação do receptor ou autorização de guiamento. O próximo incremento é espacializar a rede e os caminhos de falha e, sobre a cadeia verificada, formar os solvers dimensionados de curva embutida, base larga/passante e ESD.
