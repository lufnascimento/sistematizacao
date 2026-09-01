# Motor PCE0 e PCX0

## 1. Objetivo e limite

`PCE0` e `PCX0` sao nucleos deterministas de triagem. Eles tornam duas contas
basicas reproduziveis e auditaveis, mas nao dimensionam uma obra, nao aprovam
um receptor e nao liberam linhas para guiamento ou execucao.

- `PCE0_RUSLE_SCREENING_ONLY`: calcula o produto dos fatores RUSLE e, quando
  fornecida, compara a perda media estimada com uma tolerancia declarada.
- `PCX0_EVENT_MASS_BALANCE_ONLY`: fecha o balanco volumetrico de um evento a
  partir de chuva-excesso ja calculada, contribuicao externa, descarga
  controlada e variacao de armazenamento.

Toda saida carrega as limitacoes `NOT_PROJECT_EXECUTIVE`,
`NOT_HYDRAULIC_CAPACITY`, `NOT_SECTION_DIMENSIONING`,
`NOT_RECEIVER_APPROVAL` e `NOT_GUIDANCE_AUTHORIZED`. Qualquer pedido que tente
promover uma dessas alegacoes falha fechado.

## 2. PCE0: triagem RUSLE

O nucleo calcula:

```text
A = R * K * LS * C * P
```

em que `A` e a perda media anual de solo em `t/ha/ano`. Cada fator deve ter
fonte e estado de evidencia declarados. A tolerancia de perda de solo e
opcional; quando ausente, o motor retorna `TOLERANCE_NOT_DECLARED` e nao inventa
uma classificacao.

Esse calculo cobre apenas erosao laminar e em sulcos no dominio da RUSLE. Nao
representa ravinas, voçorocas, vazao concentrada, volume de enxurrada, capacidade
de canal ou estabilidade do receptor. O [USDA-NRCS National Agronomy
Manual](https://www.nrcs.usda.gov/sites/default/files/2022-10/National-Agronomy-Manual.pdf)
e a referencia de limite do metodo.

## 3. PCX0: balanco de massa do evento

Para cada intervalo, o volume de chuva-excesso e:

```text
V_excesso = chuva_excesso_mm / 1000 * area_contribuinte_m2
```

O residuo do evento e:

```text
residuo = V_excesso + V_entrada_externa - V_saida_controlada - delta_armazenamento
```

O nucleo verifica continuidade do armazenamento entre intervalos, publica
volumes, taxas medias e residuo relativo. Ele nao escolhe nem valida o metodo de
chuva-excesso, nao gera hidrograma, nao faz propagacao, nao calcula nivel,
velocidade, tensao, bordo livre, secao ou caminho de falha. Portanto, um balanco
numerico `PASS` nao equivale a capacidade hidraulica aprovada.

A separacao entre controle de erosao e controle de enxurrada segue a estrutura
PCE/PCX apresentada pelo [IAC, Boletim Tecnico
216](https://www.iac.sp.gov.br/publicacoes/publicacoes/iacbt126.pdf).

## 4. Contratos e execucao

Arquivos publicos:

- `schemas/pce-pcx-screening-request.schema.json`: pedido fechado e suas fontes.
- `schemas/pce-pcx-screening-stage.schema.json`: resultado e limites obrigatorios.
- `config/exemplo_pce_pcx_screening.json`: caso sintetico analitico, sem dados de
  propriedade.
- `scripts/pce_pcx.py`: funcoes puras.
- `scripts/run_pce_pcx_screening.py`: validacao, linhagem e persistencia atomica.

Execucao do exemplo:

```powershell
python .\scripts\run_pce_pcx_screening.py `
  --request .\config\exemplo_pce_pcx_screening.json `
  --output .\platform_runtime\pce_pcx_screening.json `
  --check
```

O pedido referencia o pedido canonico do projeto por caminho e SHA-256. Mudanca
no pedido de origem, campo desconhecido, chave JSON duplicada, descontinuidade
de armazenamento ou tentativa de autorizacao invalida a rodada.

## 5. Blockers que permanecem

Para evoluir de `PCE0/PCX0` para PCE/PCX de projeto ainda faltam:

1. fatores RUSLE espacializados e calibracao/validacao local;
2. chuva de projeto, distribuicao temporal e metodo de chuva-excesso aprovado;
3. hidrogramas e propagacao por alcance hidraulico;
4. secoes novas e degradadas, rugosidade, sedimento e obstrucao;
5. nivel, velocidade, tensao, bordo livre e caminho de excedencia;
6. receptores levantados, estabilizados, permitidos e aceitos;
7. incerteza, sensibilidade, vistoria e responsabilidade profissional.

Somente depois desses gates os solvers C1, C2 e C3 podem usar PCE/PCX para
comparar curva embutida, base larga/passante e ESD como alternativas de projeto.
