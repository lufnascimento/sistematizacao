# Organizacao do repositorio

## Diretorios versionados

| Caminho | Responsabilidade |
|---|---|
| `platform/` | API FastAPI, worker local, interface operacional e testes da plataforma |
| `scripts/` | motores geoespaciais, geradores, validadores e relatorios |
| `tests/` | testes unitarios e de contrato dos motores GIS |
| `config/` | catalogos, presets e exemplos de pedidos imutaveis |
| `schemas/` | contratos JSON Schema dos produtos e estagios |
| `docs/` | pesquisa, arquitetura, regras agronomicas, gaps e roadmaps |
| `dataset/` | somente documentacao; os dados e derivados permanecem locais |

A aplicacao operacional e servida por `python platform/run.py`. A demonstracao
legada S0-S6 foi retirada da raiz e permanece somente em
`legacy/demo-static/` no workspace local, porque incorpora mapas e metricas de
um caso de cliente.

## Arquivos locais

`platform_runtime/`, uploads, estados da API, caches, ferramentas instaladas,
`dataset/ANALISE_INICIAL.md`, produtos de `dataset/derived/` e materiais em
`dataset/private_project/` nao entram no Git. Eles sao grandes, reproduziveis ou
podem conter dados do cliente. Essa politica evita publicar coordenadas,
identificadores e diagnosticos de uma propriedade em um repositorio publico e
impede que resultados transitorios sejam confundidos com codigo-fonte.

## Verificacao antes do commit

```powershell
python -m unittest discover -s platform\tests -p "test_*.py" -v
& 'C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat' -m unittest discover -s tests -p "test_*.py" -v
git status --short --ignored
```

O E2E completo de cenarios usa dados reais locais e pode levar algumas horas.
Seus artefatos devem permanecer fora do historico Git e ser publicados por um
canal de entregas controlado quando necessario.
