# TerraFlux Platform

Aplicacao local funcional para cadastrar projetos, receber insumos, configurar
uma rodada, congelar pedidos, executar motores geoespaciais em fila e baixar os
produtos verificados.

## Executar

Na raiz do repositorio:

```powershell
$python = "python"
& $python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r platform\requirements.txt
$env:TERRAFLUX_DATA_ROOT = (Resolve-Path .).Path + '\platform_runtime\local'
& .\.venv\Scripts\python.exe platform\run.py
```

Aplicacao: `http://127.0.0.1:8000/`

Documentacao da API: `http://127.0.0.1:8000/api/docs`

O worker geoespacial procura o QGIS em
`C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat`. Outro caminho pode ser
declarado em `TERRAFLUX_QGIS_PYTHON`.

## Fluxo operacional

1. Criar o projeto e declarar o CRS projetado em metros.
2. Enviar os poligonos dos talhoes e LAS/LAZ ou MDE/MDT.
3. Configurar resolucao, coluna de identificacao, espacamento, cabeceira, raio,
   tiro minimo, objetivos e restricoes.
4. Consultar a prontidao por produto.
5. Congelar configuracao, assets e checksums em um pedido imutavel.
6. Executar o motor permitido e acompanhar status e logs.
7. Comparar os cenarios e baixar mapas, GeoPackages, rasters, manifestos e o
   dossie PDF da rodada.

Para estudar a resposta da chuva, o fluxo e independente da topografia: em
Configurar, habilite **Calcular a parcela que escoa**, informe area contribuinte,
CN, `Ia/S`, intervalo, serie de chuva e fonte. Para obter vazao no tempo,
habilite tambem **Calcular vazao ao longo do tempo** e informe o tempo de
resposta da area. Em Produtos, escolha **Chuva que vira escoamento** e,
opcionalmente, **Hidrograma preliminar** e **Propagacao preliminar na rede**.
O terceiro produto exige no de entrada, conectividade e tempo de viagem de
cada trecho. Resultados mostra chuva, excesso, volume, pico, rede e downloads. Esses produtos nao exigem LAZ, mas tambem
nao espacializam alcances nem dimensionam estruturas.

## Motores habilitados

- `validate_uploads`: integridade, papeis e prontidao dos insumos.
- `project_topography`: MDT, declividade, relevo sombreado, curvas, mapas e
  densidade quando a origem e LAS/LAZ.
- `project_pipeline_e0`: topografia da rodada, seis alternativas geometricas
  E0, familia curva continua CF0 e triagem conceitual C1, conforme os produtos
  selecionados; ao final, consolida resultados e evidencias em um dossie PDF
  pesquisavel.
- `project_hydrology_screening`: transforma o hietograma declarado em
  chuva que vira escoamento e, quando solicitado, em hidrograma preliminar com
  pico e conservacao de volume e, quando configurado, desloca e acumula a onda
  em uma rede aciclica. Atenuacao, remanso, secoes e receptores permanecem
  explicitamente bloqueados.
- `demo_current_dataset`: publica somente artefatos demonstrativos presentes em
  uma lista fechada no workspace local. Esses dados nao acompanham o repositorio
  publico.

`SULCATION_E0`, `CF0_CONTINUOUS` e `C1_EMBEDDED_SCREENING` exigem MDT e grade de calculo com resolucao
igual ou menor que o espacamento entre linhas. A resolucao preferencial e igual
ou menor que metade do espacamento; entre metade e um espacamento inteiro o
resultado e aceito como resolucao degradada e precisa de revisao. Os tres exigem
coluna identificadora dos talhoes, rede eletrica declarada inexistente e
continuidade restrita ao talhao nesta versao. C1 exige que CF0 esteja selecionado
na mesma rodada.

Para MDE/MDT raster, o gate usa a resolucao efetiva: o maior valor entre o pixel
nativo da fonte e a grade de saida. Reamostrar um raster de 3,4 m para pixels de
1 m nao habilita sulcacao de 1,5 m. Quando a origem e LAS/LAZ, o MDT usa somente
`Classification[2:2]`; uma nuvem sem pontos de solo classificados falha fechada.

Quando uma rede eletrica e enviada, a geracao permanece bloqueada por
`POWER_BARRIER_STAGE_REQUIRED`. O dado nao e ignorado: falta implementar e
validar o estagio que transforma eixos/postes em barreiras e divide as linhas.

## Fronteira tecnica

Os produtos atuais sao triagem E0/CF0, C1 conceitual, chuva que vira escoamento
e hidrograma preliminar. Eles nao autorizam
guiamento de maquina, projeto agronomico executivo, aprovacao hidraulica ou
locacao em campo. C1 publica sensibilidades TI e registra TD como bloqueado;
PCE/PCX completos, secoes e espacamentos ainda nao sao dimensionados. O pico
preliminar nao equivale a capacidade hidraulica aprovada.

Curva embutida dimensionada, base larga/passante, ESD/canal escoadouro, POA
dinamico e continuidade entre talhoes/propriedades continuam visiveis no
catalogo, mas bloqueados ate seus dados e motores especificos existirem.

## Testes

```powershell
python -m unittest discover -s platform\tests -p "test_*.py" -v
python platform\tests\e2e_topography_api.py
python platform\tests\e2e_scenario_pipeline_api.py
python platform\tests\verify_platform_frontend.py
```

Para instalar tambem as dependencias dos verificadores de navegador:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r platform\requirements-dev.txt
& .\.venv\Scripts\python.exe -m playwright install chromium
```

O E2E de cenarios usa timeout longo porque E0, CF0 e C1 processam geometrias
reais. A integracao C1 permanece `CONCEPT_ONLY` mesmo quando o job e concluido.

Ao reiniciar o worker, rodadas que estavam apenas enfileiradas voltam para a
fila. Rodadas que estavam em processamento sao marcadas como interrompidas e
suas publicacoes parciais sao removidas; o usuario deve iniciar uma nova rodada.
