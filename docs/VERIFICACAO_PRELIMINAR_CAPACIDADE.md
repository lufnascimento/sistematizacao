# Verificacao preliminar de capacidade

Este produto compara o pico propagado em cada trecho com uma secao trapezoidal
declarada pelo usuario. Para cada trecho sao informados largura de fundo,
inclinacao lateral, declividade, rugosidade de Manning e profundidade util.

O motor calcula area molhada, perimetro molhado, raio hidraulico, capacidade na
profundidade disponivel e profundidade normal requerida para o pico. Tambem
publica velocidade, numero de Froude e tensao media de contorno como
diagnosticos. Uma mesma rede pode conter secoes nos estados **nova**, **atual**
e **degradada**, permitindo comparar perda de capacidade sem duplicar trechos.

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

O proximo incremento deve testar borda livre, condicoes de jusante, transicoes
e caminho de excedencia. Depois disso sera possivel iniciar a comparacao
hidraulica das estruturas de curva embutida, base larga/passante e ESD.

Referencias metodologicas:

- [NRCS National Engineering Handbook, Chapter 7 - Grassed Waterways](https://directives.nrcs.usda.gov/sites/default/files2/1748618373/Chapter%207%20%E2%80%93%20Grassed%20Waterways.pdf)
- [NRCS National Engineering Handbook, Chapter 8 - Threshold Channel Design](https://directives.nrcs.usda.gov/sites/default/files2/1720613324/Chapter%2008%20-%20Threshold%20Channel%20Design.pdf)
