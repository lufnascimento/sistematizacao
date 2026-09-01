# Produtos derivados

Os arquivos desta pasta são saídas calculadas dos dados-fonte em `dataset`.

## Pipeline ativo

- `audit_metrics.json`
- `source_product_metrics.json`
- `dtm_1m.tif`
- `density_5m.tif`
- `density_points_per_km2_5m.tif`
- `slope_dem_percent.tif`
- `slope_dem_degrees.tif`
- `contours_1m.gpkg`
- `contributing_area_ha.tif`
- `sulcation_scenarios.gpkg`
- `sulcation_scenario_metrics.json`
- `sulcation_scenarios_map.png`
- `multifield_connection_screening.json`
- `multifield_connection_screening.gpkg`
- mapas PNG referenciados pelo `index.html`

Arquivos iniciados por `_legacy_pdf_` são análises antigas dos exemplos visuais e não participam do pipeline, da interface ou dos critérios de aceitação. Eles não devem ser interpretados como produtos da plataforma.

Arquivos iniciados por `screening_` são experimentos de sensibilidade sem rede vetorial validada. O produto ativo de triagem é `contributing_area_ha.tif`, sempre acompanhado dos alertas de contexto incompleto.

Resultados de fluxo e bacias permanecem em nível de triagem enquanto o levantamento não abranger toda a área contribuinte e os exutórios não forem verificados.

Os arquivos `sulcation_*` são primitivas geométricas `E0`. Eles comparam famílias candidatas e seus perfis sobre o MDT, mas ainda não representam os macrocenários `C1_CURVA_EMBUTIDA`, `C2_BASE_LARGA_PASSANTE`, `C3_ESD` ou `C4_MISTO_POR_ZONA`. Não possuem aprovação hidráulica, agronômica ou de máquina. Devem ser reproduzidos e verificados com:

```powershell
& 'C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat' '.\scripts\generate_sulcation_scenarios.py'
& 'C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat' '.\scripts\verify_sulcation_scenarios.py'
```

`multifield_connection_screening.json` mede apenas oportunidades geométricas `UNCONFIRMED` entre os dois talhões atuais. O GeoPackage correspondente separa `e0g_work_segments` de `e0g_movement_connectors`; uma divisa comum não comprova que exista carreador, portal seguro ou autorização. Reproduza os dois depois das linhas E0 com:

```powershell
& 'C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat' '.\scripts\analyze_multifield_connections.py'
```

`embedded_terrace_screening.*` é o precursor geométrico `C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED`. A grade vertical de 2/4/6 m é somente sensibilidade de leitura do relevo, não espaçamento de terraços nem PCE/PCX. A variante TI possui eixos e faixas diagnósticas; TD, seção, superfície proposta, hidráulica e guiamento não foram gerados ou autorizados. Reproduza e verifique com:

```powershell
& 'C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat' '.\scripts\generate_embedded_terrace_screening.py'
& 'C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat' '.\scripts\verify_embedded_terrace_screening.py'
```
