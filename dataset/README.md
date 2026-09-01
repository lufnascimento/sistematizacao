# Dados locais

Esta pasta recebe os insumos geoespaciais e os produtos derivados usados nas
validacoes locais do TerraFlux. Os arquivos da fazenda nao sao versionados
porque podem conter coordenadas, informacoes de cliente e binarios grandes.

## Estrutura esperada

- `Contorno.*`: pacote vetorial completo dos talhoes, ou arquivo ZIP/GPKG/GeoJSON equivalente.
- `TERRENO_LIMPO.LAZ`: nuvem LAS/LAZ classificada, quando usada como fonte altimetrica.
- `DEM.tif`: MDE/MDT opcional ou alternativo a nuvem.
- `derived/`: mapas, rasters, GeoPackages, manifestos e PDFs reproduzidos pelos motores.

Os nomes acima descrevem o dataset de validacao atual. Na plataforma, o cliente
pode usar outros nomes; o papel de cada upload e registrado pela API.

Nenhum dado ignorado pelo Git e apagado pelos scripts de organizacao. Para
reproduzir os produtos, consulte os comandos no `README.md` da raiz e em
`platform/README.md`.
