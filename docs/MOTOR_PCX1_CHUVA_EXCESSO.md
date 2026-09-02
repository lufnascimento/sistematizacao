# Motor PCX1 de chuva-excesso

## 1. O que foi implementado

`PCX1_NRCS_CN_RAINFALL_EXCESS_ONLY` transforma um hietograma incremental de
chuva em chuva-excesso incremental pelo metodo NRCS Curve Number. A conta e
deterministica, preserva todos os intervalos e publica a profundidade e o volume
de escoamento gerado antes de qualquer propagacao.

O metodo segue a formulacao de evento descrita no [USDA-NRCS National
Engineering Handbook, Part 630](https://www.nrcs.usda.gov/conservation-basics/conservation-by-state/wisconsin/news/part-630-hydrology-national-engineering-handbook).
A escolha e a parametrizacao do metodo continuam sujeitas a aprovacao do
projeto; um Curve Number de preset nunca vira evidencia local automaticamente.

## 2. Jornada do usuario na plataforma

1. Em **Dados**, o usuario envia solo, chuva e bacia quando disponiveis. Esses
   arquivos permanecem como evidencia; PCX1 tambem aceita digitacao manual de
   um evento para triagem.
2. Em **Configurar > Chuva e escoamento PCX1**, habilita o calculo e informa:
   area contribuinte, Curve Number, razao `Ia/S`, duracao do intervalo, chuva de
   cada intervalo e identificador da fonte.
3. Escolhe o estado da evidencia: `PROJECT_EVIDENCE` ou `E0_ASSUMPTION`.
   Hipotese E0 calcula, mas mantem o blocker de evidencia.
4. Em **Produtos**, seleciona exclusivamente **Chuva-excesso PCX1**. A
   plataforma bloqueia o cartao enquanto o evento estiver incompleto.
5. Em **Executar**, compila o pedido imutavel. Area, parametros, hietograma,
   fonte e produtos recebem hash e nao podem mudar durante a rodada.
6. Em **Resultados**, ve chuva total, chuva-excesso, volume gerado, coeficiente
   do evento e todos os blockers. Pode baixar o manifesto JSON e a serie CSV.

PCX1 e um produto hidrologico independente da topografia E0. Ele pode ser
executado sem LAZ quando area e evento forem fornecidos, mas isso nao autoriza a
aplicacao espacial do resultado a talhoes ou alcances hidraulicos.

## 3. Calculo

```text
S = 25400 / CN - 254
Ia = lambda * S

Q(P) = 0                              , P <= Ia
Q(P) = (P - Ia)^2 / (P - Ia + S)      , P > Ia
```

`P` e a chuva acumulada em milimetros e `Q` e a chuva-excesso acumulada. Em
cada intervalo, PCX1 publica `Q_i - Q_(i-1)`. O volume e calculado pela area
contribuinte declarada. Os incrementos sao nao negativos e reconciliam com a
equacao acumulada.

O valor `Ia/S = 0,20` aparece como ponto inicial editavel na interface, com
estado `E0_ASSUMPTION`. Ele nao e adotado como verdade do projeto. Alterar
`Ia/S`, CN, chuva ou area cria outro pedido e outro resultado.

## 4. Produtos acessiveis

| Produto | Conteudo | Uso permitido |
|---|---|---|
| `pcx1_rainfall_excess.json` | entradas, fonte, linhagem, resumo, intervalos, limites e blockers | auditoria e integracao com o proximo motor |
| `pcx1_rainfall_excess_intervals.csv` | chuva, intensidade, perdas e excesso por intervalo | grafico, conferencia e comparacao de eventos |
| resumo na plataforma | chuva, excesso, volume e coeficiente do evento | leitura rapida de triagem |

O contrato de saida e `schemas/pcx1-rainfall-excess-stage.schema.json`.

## 5. O que PCX1 nao faz

PCX1 nao calcula tempo de concentracao, hidrograma, vazao de pico, propagacao,
armazenamento fisico, nivel, velocidade, tensao, bordo livre, erosao
concentrada, secao, receptor ou caminho de falha. Tambem nao seleciona curva
embutida, base larga/passante ou ESD.

O resultado sempre carrega:

- `NO_HYDROGRAPH` e `NO_PEAK_FLOW`;
- `NO_HYDRAULIC_ROUTING` e `NOT_HYDRAULIC_CAPACITY`;
- `NOT_SECTION_DIMENSIONING` e `NOT_RECEIVER_APPROVAL`;
- `NOT_PROJECT_EXECUTIVE` e `NOT_GUIDANCE_AUTHORIZED`.

## 6. Proximo encadeamento

O proximo motor deve receber os intervalos de PCX1 e gerar um hidrograma
reproduzivel com tempo de concentracao/lag declarado. Depois entram propagacao
por alcance, secoes novas e degradadas, receptores, excedencia e incerteza. So
essa cadeia completa pode alimentar comparacoes dimensionadas C1, C2 e C3.
