# Verificacao preliminar de capacidade

Este produto compara o pico propagado em cada trecho com uma secao trapezoidal
declarada pelo usuario. Para cada trecho sao informados largura de fundo,
inclinacao lateral, declividade, rugosidade de Manning e profundidade util.

O motor calcula area molhada, perimetro molhado, raio hidraulico, capacidade na
profundidade disponivel e profundidade normal requerida para o pico. Tambem
publica velocidade, numero de Froude e tensao media de contorno como
diagnosticos. Uma mesma rede pode conter secoes nos estados **nova**, **atual**
e **degradada**, permitindo comparar perda de capacidade sem duplicar trechos.
Quando a profundidade ate a margem e a borda livre requerida sao informadas,
o motor separa margem atendida, margem insuficiente e transbordamento.
Tambem pode receber uma lamina conhecida a jusante e um coeficiente de perda
em transicao. O envelope soma a perda baseada na diferenca entre cargas de
velocidade e usa a maior profundidade entre a normal e a declarada a jusante.

Limites de velocidade e tensao podem ser declarados por estado. Cada limite
exige uma fonte e deve ser marcado como referencia do sistema ou evidencia do
projeto. O resultado informa se o valor calculado esta dentro ou acima do
limite declarado, mas `erosion_safety_approved` permanece sempre falso neste
estagio.

A base matematica e a equacao de Manning para escoamento uniforme, usada pelo
[HEC-RAS para dimensionamento preliminar de canais](https://www.hec.usace.army.mil/confluence/rasdocs/ras1dtechref/6.5/stable-channel-design-functions/uniform-flow-computations).
A profundidade normal e obtida iterativamente. O proprio material do HEC-RAS
alerta que a hipotese de profundidade normal pode introduzir incerteza quando a
condicao de jusante controla o escoamento.

Exemplo de entrada:

```json
[
  {
    "id": "TRECHO_01",
    "condition_state": "CURRENT",
    "bottom_width_m": 0.5,
    "side_slope_h_to_v": 1.5,
    "slope_m_m": 0.005,
    "manning_n": 0.04,
    "maximum_flow_depth_m": 0.6,
    "bankfull_depth_m": 0.85,
    "required_freeboard_m": 0.2,
    "overflow_path_state": "DECLARED_NOT_REVIEWED",
    "overflow_receiver_id": "BACIA_SEGURA_01",
    "downstream_water_depth_m": 0.35,
    "downstream_velocity_m_s": 0.2,
    "transition_loss_coefficient": 0.3,
    "downstream_boundary_source_id": "levantamento-jusante-2026-08",
    "downstream_boundary_evidence_state": "PROJECT_EVIDENCE",
    "maximum_admissible_velocity_m_s": 1.2,
    "maximum_admissible_shear_pa": 20.0,
    "stability_limit_source_id": "regra-regional-revisao-3",
    "stability_limit_evidence_state": "PROJECT_EVIDENCE"
  }
]
```

Os IDs precisam coincidir exatamente com os trechos da rede. O sistema publica
`verificacao_preliminar_capacidade.json` e `capacidade_por_trecho.csv`.

O estado **com folga** significa apenas que a vazao cabe na geometria declarada
sob escoamento permanente e uniforme. Ainda nao considera remanso, onda nao
permanente, transicoes, curvas, obstrucoes, sedimento, degradacao, borda livre,
erosao admissivel, extravasamento, caminho de falha ou seguranca do receptor.
Por isso nenhum trecho e aprovado para execucao ou guiamento nesta etapa.

Referencias publicas podem preencher modelos de comparacao, mas nao substituem
solo, cobertura vegetal, duracao do escoamento, estado de manutencao e revisao
profissional locais. A NRCS ressalta que limites variam com material,
vegetacao, duracao e condicao do local e recomenda fator de seguranca para
valores tabelados.

Um caminho de excedencia declarado registra o destino pretendido da agua, mas
nao simula a mancha, a velocidade fora da secao, a erosao no percurso nem a
capacidade do receptor. Mesmo `PROJECT_REVIEWED` permanece sem aprovacao
hidraulica nesta entrega.

Esse envelope nao resolve o perfil gradualmente variado nem propaga o remanso
entre secoes. O HEC-RAS alerta que condicoes de contorno mal definidas podem
introduzir erro e instabilidade; portanto a origem da lamina e obrigatoria.

O proximo incremento deve resolver o perfil entre secoes e gerar a geometria
espacial do caminho de excedencia. Depois disso sera possivel iniciar a comparacao
hidraulica das estruturas de curva embutida, base larga/passante e ESD.

Referencias metodologicas:

- [NRCS National Engineering Handbook, Chapter 7 - Grassed Waterways](https://directives.nrcs.usda.gov/sites/default/files2/1748618373/Chapter%207%20%E2%80%93%20Grassed%20Waterways.pdf)
- [NRCS National Engineering Handbook, Chapter 8 - Threshold Channel Design](https://directives.nrcs.usda.gov/sites/default/files2/1720613324/Chapter%2008%20-%20Threshold%20Channel%20Design.pdf)
- [HEC-RAS - Downstream Boundary Condition Considerations](https://www.hec.usace.army.mil/confluence/rasdocs/ras1dtechref/latest/performing-a-dam-break-study-with-hec-ras/downstream-boundary-condition-considerations)
- [HEC-RAS - Contraction and Expansion Loss Evaluation](https://www.hec.usace.army.mil/confluence/rasdocs/ras1dtechref/6.1/theoretical-basis-for-one-dimensional-and-two-dimensional-hydrodynamic-calculations/1d-steady-flow-water-surface-profiles/contraction-and-expansion-loss-evaluation?scroll-versions%3Aversion-name=6.7_beta4)
