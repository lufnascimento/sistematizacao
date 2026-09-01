# Produto Analítico de Cenários de Sulcação

## 1. Decisão central

O produto não deve procurar uma única “melhor sulcação”. Ele deve gerar famílias completas de linhas, rejeitar alternativas inviáveis e entregar uma pequena carteira de cenários não dominados, deixando explícita a troca entre:

- conservação do solo e controle da água;
- colheitabilidade e tráfego controlado;
- capacidade operacional;
- área produtiva;
- intervenções em terraços, carreadores e drenagem;
- robustez diante da incerteza dos dados.

A solução é tecnicamente viável como gerador e comparador assistido. Somente polígono e LAZ permitem uma triagem topográfica e operacional. Eles não permitem aprovar o comportamento hidráulico nem liberar linhas para piloto automático.

O resultado correto é:

```text
cenário = referência
        x sistema conservacionista
        x geometria da sulcação
        x nível de intervenção
        x conexão operacional
        x perfil da frota
        x envelope de incerteza
```

Para o cliente, a comparação terá obrigatoriamente três macrocenários: **curva embutida**, **base larga/passante** e **ESD**. Quando o relevo ou os solos pedirem soluções diferentes dentro da mesma fazenda, será incluído também o **misto por zonas**.

Internamente, o motor continua separando função hidráulica, seção construtiva e preferência operacional. Essa separação é necessária porque uma seção embutida ou de base larga pode funcionar como terraço de infiltração ou de drenagem, conforme PCE/PCX; “passante” informa como a máquina cruza a estrutura; e ESD é um sistema completo. “Tiros longos” permanece como objetivo operacional aplicado dentro de cada macrocenário.

As regras e provas exigidas em cada etapa estão consolidadas na [Matriz Agronômica de Conservação e Colheitabilidade](MATRIZ_AGRONOMICA_CONSERVACAO_COLHEITABILIDADE.md).

## 2. O que o CAD ensina e o que ele não resolve

O fluxo ensinado pelo AgroCAD confirma uma sequência de projeto útil: analisar escoamento e perda potencial de solo; dividir os talhões; projetar e suavizar curvas; medir desvios horizontais e verticais; gerar bordaduras e cópias paralelas; analisar angulação, comprimento e declividade longitudinal; estimar manobras, tempo e combustível; e comparar o executado com o projetado por paralelismo e sobreposição. [Treinamento oficial AgroCAD](https://agrocad.com.br/?page_id=263)

No Civil 3D, `Feature Lines` são objetos 3D que podem obter elevação de uma superfície e expor comprimento, cota e greide. O `Stepped Offset` cria uma paralela independente com distância e diferença de elevação. Isso é muito útil para inspeção, perfis, edição e intercâmbio, mas não é um algoritmo agronômico de otimização. [Feature Lines](https://help.autodesk.com/view/CIV3D/2024/ENU/?guid=GUID-89F4D3FC-5E3E-4047-9516-CCF6CE1A9345), [Stepped Offset](https://help.autodesk.com/view/CIV3D/2024/ENU/?guid=GUID-5D15FCE9-148D-484D-9E70-91DA6DBD0C78)

O CAD deve ser tratado como bancada de revisão:

1. importar MDT/TIN, limites, obstáculos e estruturas;
2. visualizar curvas e drenagem;
3. desenhar ou revisar curvas-guia;
4. gerar paralelas e perfis;
5. corrigir trechos locais;
6. exportar DXF/LandXML e relatórios.

Ele não garante, sozinho, que todas as paralelas conservem espaçamento, raio, greide, saída hidráulica e envelope da frota. Também não escolhe automaticamente a melhor decomposição do terreno nem prova a capacidade das estruturas.

## 3. A base algorítmica mais promissora

Há literatura diretamente aplicada à cana-de-açúcar em relevo ondulado de São Paulo. O método publicado por Spekken, De Bruin, Molin e Sparovek possui três passos: construir referências e curvas de nível híbridas; transformar essas curvas em trajetórias paralelas dirigíveis; e avaliar acúmulo de fluxo e suscetibilidade à perda de solo. Nos três estudos de caso, o modelo indicou reduções de perda de solo de até 75% frente ao arranjo humano usado na comparação. Esse número é evidência do método nos casos estudados, não uma promessa para qualquer fazenda. [Artigo revisado por pares](https://research.wur.nl/en/publications/planning-machine-paths-and-row-crop-patterns-on-steep-surfaces-to/)

O trabalho precursor descreve uma implementação aproveitável:

1. extrair curvas de nível adjacentes;
2. simplificá-las com Douglas-Peucker;
3. deslocá-las progressivamente uma em direção à outra;
4. usar as interseções dos offsets como vértices médios;
5. ponderar a forma para aderir mais às referências situadas nas regiões críticas;
6. hibridizar recursivamente todas as referências até obter uma curva-semente;
7. suavizar a semente até que ela e suas paralelas respeitem a dirigibilidade;
8. cobrir o domínio de trabalho e validar cada paralela, não somente a semente.

Offsets sequenciais simples falham em concavidades: as linhas convergem, o espaçamento diminui e aparecem mudanças bruscas de direção. Esse é um dos motivos para não reproduzir o processo manual como uma simples sequência de comandos `OFFSET`. [Método de curva híbrida e dirigibilidade](https://www.agriculturadeprecisao.org.br/wp-content/uploads/2020/01/cgr-2013_05.pdf)

Algoritmos de cobertura 3D também reforçam que o talhão pode receber preferências locais associadas a sub-regiões topográficas e a diferentes curvas-semente. Essas sub-regiões orientam o campo, mas não recortam o domínio de trabalho nem autorizam término, reinício ou manobra. Em experimentos publicados, a consideração do terreno 3D reduziu simultaneamente custos de manobra e de erosão quando comparada ao planejamento 2D. [ASABE, cobertura em terreno 3D](https://elibrary.asabe.org/abstract.asp?aid=29833&redir=&redirType=&t=2)

## 4. Modelo geométrico recomendado

### 4.1 Área operável

Para cada domínio operacional delimitado por superfícies físicas:

```text
P_operável = buffer(limite_externo, -cabeceira)
           - união(buffer(obstáculo_i, afastamento_i))
```

As ilhas não podem ser consideradas obstáculos reais sem classificação. Cada vazio deve virar árvore, pedra, estrutura, erosão, água, erro de vetor ou área atravessável.

### 4.2 Campo de direção

No MDT suavizado, seja `g = grad(z)`. A direção tangente ao contorno é:

```text
t = (-g_y, g_x) / ||g||
```

Uma direção candidata com pequeno greide dirigido pode ser construída por:

```text
d = cos(beta) t + sin(beta) u
beta = asin(greide_alvo / ||g||)
```

onde `u` aponta para o receptor aprovado. O greide-alvo é um parâmetro do projeto e não uma regra universal.

Para evitar saltos artificiais de 180 graus, a orientação deve ser regularizada como campo não orientado usando `(cos(2 theta), sin(2 theta))`.

### 4.3 Campo de fase para gerar a família inteira

Uma forma robusta de obter linhas aproximadamente paralelas é resolver um campo escalar `phi` cuja normal acompanha o campo de direção desejado:

```text
min_phi  soma w_i ||grad(phi_i) - n_i||²
       + lambda_s ||H(phi_i)||²
       + lambda_a ||phi_i - phi_ancora||²
```

As linhas são isolinhas:

```text
phi = phi_0 + k * espaçamento
```

Antes da extração executiva, deve-se resolver ou reinicializar um campo do tipo distância/eikonal, ou relaxar geometricamente as linhas, para controlar `||grad(phi)||` próximo de 1. Uma simples reescala dos níveis de `phi` não basta quando o gradiente varia ao longo da mesma isolinha; nesses casos, a distância física entre linhas pode comprimir ou dilatar nas zonas curvas. Esse problema apareceu no experimento atual.

### 4.4 Preferências locais em domínio contínuo

Uma direção uniforme é insuficiente quando existem espigões, concavidades, obstáculos e receptores distintos. O campo pode combinar preferências locais derivadas de:

- faces topográficas e mudanças relevantes de aspecto;
- divisores e corredores de convergência;
- terraços, estradas, carreadores e canais existentes;
- obstáculos e reentrâncias do limite;
- portais mecanizáveis entre talhões;
- custo de transicionar suavemente entre duas preferências.

Essas zonas são suportes analíticos de pesos e direções dentro de um único domínio contínuo. Sua fronteira nunca recorta uma linha, cria endpoint, reinicia o trabalho ou vira superfície de manobra. Só limites físicos classificados, como borda operável, obstáculo, terraço não passante, carreador ou barreira, podem terminar um segmento. O motor deve otimizar simultaneamente a variação espacial das preferências e a qualidade da família contínua; transições excessivamente bruscas aumentam curvatura, enquanto variação insuficiente pode produzir linhas topográfica e hidraulicamente ruins.

## 5. Pipeline do gerador

### Etapa A: preparação

1. Validar CRS, unidades, datum vertical, precisão e cobertura integral do domínio pelo MDT, sem células `NoData` na área operável.
2. Produzir MDT original, MDT suavizado para orientação e MDT condicionado para água.
3. Classificar limites, vazios, cabeceiras, estruturas, entradas e receptores.
4. Construir área operável e envelope de incerteza.

### Etapa B: candidatos

1. Gerar baselines retas em várias orientações.
2. Gerar referências de contorno e greide dirigido.
3. Importar linhas existentes e manuais como referências independentes.
4. Hibridizar referências e formar curvas-semente com preferências locais no domínio contínuo.
5. Criar a família inteira no espaçamento nominal.
6. Recortar linhas, resolver bordas e conectar somente portais permitidos.

### Etapa C: dirigibilidade

1. Suavizar respeitando desvio lateral máximo.
2. Medir curvatura, raio e variação de curvatura.
3. Simular o envelope da colhedora, plantadora, sulcador e transbordos.
4. Gerar cabeceiras e manobras cinematicamente possíveis.
5. Calcular falhas, sobreposições e invasão da faixa de cultivo.

### Etapa D: água e solo

1. Dividir cada linha-guia em segmentos trabalhados e alcances hidráulicos.
2. Conectar cada alcance a armazenamento calculado ou receptor estável.
3. Queimar sulcos, terraços e estruturas em uma superfície proposta.
4. Recalcular escoamento, vazões, capacidade e erosão.
5. Testar chuva, infiltração, obstrução e tolerância construtiva em envelope.

### Etapa E: seleção

1. Eliminar violações duras.
2. Remover alternativas quase idênticas por epsilon-dominância.
3. Construir a fronteira de Pareto.
4. Entregar até cinco representantes: conservação, colheitabilidade, capacidade, menor intervenção e equilíbrio.

## 6. Carteira obrigatória de cenários

### 6.1 Cenário C1: curva embutida

O cenário usa terraços de seção embutida e gera a sulcação separadamente em cada faixa entre estruturas. Por padrão operacional, as linhas são interrompidas no terraço; uma travessia só existe quando for projetada. O PCE/PCX decide se cada estrutura será TI, com armazenamento e infiltração, ou TD, com descarga em receptor dimensionado.

O gerador deve calcular alinhamento, espaçamento vertical, seção, superfície proposta, área perdida, corte/aterro, capacidade, extravasamento, segmentos trabalhados, endpoints e manobras. O canal mais profundo e a faixa não plantável precisam entrar no custo do cenário.

### 6.2 Cenário C2: base larga/passante

O cenário usa seção larga e suave e permite que linhas operacionais atravessem a estrutura somente em `crossing_nodes` validados. Mesmo quando a linha-guia continua, o alcance hidráulico do sulco termina ou é transferido na estrutura. O motor não pode confundir continuidade da máquina com continuidade da água.

Cada crossing deve ter perfil 3D, ângulo, raio, quebra vertical, envelope da frota e verificação da seção do terraço. A simulação deve ser feita tanto na seção recém-construída quanto na seção degradada por tráfego e compactação.

### 6.3 Cenário C3: ESD

O cenário atribui preferências locais a setores analíticos ESD, gera curvas-guia híbridas com pequeno greide controlado e transforma cada sulco em alcance hidráulico explícito. A fronteira entre setores não recorta a sulcação nem cria endpoint; um alcance só termina ou transfere água em estrutura ou receptor físico calculado. Cada alcance recebe uma área contribuinte, uma capacidade e um destino estável. Convergência não dimensionada, contragreide, descarga em carreador ou endpoint sem receptor reprovam a família.

O ESD pode ser `setorial`, quando os sulcos e receptores resolvem a zona, ou `complementado`, quando o cálculo exige TD, CEV, dissipação ou faixas vegetadas. Esses componentes são adicionados explicitamente; não existe regra universal dizendo que todo ESD exige CEV.

### 6.4 Cenário C4: misto por zonas

O cenário misto escolhe embutida, base larga/passante ou ESD segundo preferências topográficas, pedológicas e operacionais locais. Transições puramente analíticas são suaves dentro do domínio contínuo e não viram nós ou endpoints; somente estruturas e superfícies físicas calculadas criam nós da rede. Ele não é uma montagem visual: toda a combinação gera uma única superfície proposta e uma nova simulação hidráulica e operacional completa.

### 6.5 Fundamento técnico

O IAC exige que conservação trate conjuntamente controle da erosão e condução da enxurrada. A direção da sulcação ajuda a controlar o escoamento, mas tiros retos em maior declive podem se comportar como plantio morro abaixo. Estradas e carreadores também precisam de drenagem própria. [Boletim Técnico IAC 216](https://www.iac.sp.gov.br/publicacoes/publicacoes/iacbt126.pdf)

O mesmo boletim descreve base larga como canal mais largo e raso, embutido como seção mais estreita e profunda e passante como base larga que permite trânsito sobre o camalhão, sem dispensar seção mínima calculada no PCX. O Manual da ANA enquadra o ESD como projeto integrado de erosão e enxurrada, com alinhamentos de sulcação, drenagem de vias, hidrologia e estruturas quando necessárias. [Manual ANA, volume 5](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais/manual-programa-produtor-de-agua-volume-5)

### 6.6 Variações de objetivo dentro de cada cenário

- menor greide longitudinal;
- menor inclinação transversal;
- tiros mais longos;
- menos manobras;
- mais área produtiva;
- menor curvatura;
- eixo, fase ou rota compartilhados entre talhões;
- menor intervenção;
- maior robustez.

Essas preferências alteram o ranqueamento, nunca desligam uma restrição de segurança.

A configuração canônica desses presets está em [cenarios_conservacionistas.json](../config/cenarios_conservacionistas.json). Se um macrocenário não for elegível, ele continuará no relatório como `não aplicável`, acompanhado das regras e dados que causaram o bloqueio.

### 6.7 Continuidade multi-talhão e multifazenda

Curva embutida, base larga/passante e ESD serão comparadas também em quatro modos operacionais:

| Modo | O que o cenário representa |
|---|---|
| `OC0_ISOLADO` | cada talhão usa a melhor família local; baseline obrigatório |
| `OC1_EIXO_COMUM` | talhões compartilham orientação e fase, mas não há travessia afirmada |
| `OC2_GUIA_CONTINUA` | a máquina segue uma guia única, levanta o implemento e cruza um portal autorizado |
| `OC3_TRABALHO_CONTINUO` | o implemento permanece engatado através de um portal cultivável, dimensionado e aprovado |

O modelo preserva três geometrias diferentes: `sulco_fisico`, somente onde existe trabalho; `guia_operacional`, que pode incluir `LIFT`, `CROSS` e `TURN`; e `alcance_hidraulico`, que termina ou transfere água apenas em um nó calculado. Portanto:

```text
linha guia contínua != trabalho contínuo != alcance hidráulico contínuo
```

Para criar tiros diretos, o gerador resolve uma fase global e preserva o mesmo `row_global_id` nos dois lados do carreador. Quando cada talhão precisa de direção própria, ele pareia endpoints compatíveis por posição, tangente, fase, greide e custo de travessia. Famílias perpendiculares não formam um tiro direto; podem formar uma rota conectada `LIFT/CROSS/LIFT` se a transição de curvatura e o envelope da frota couberem no portal.

Em fazendas adjacentes cobertas pela mesma nuvem, o cálculo geométrico é possível, mas a conexão permanece bloqueada até existirem limites fundiários classificados, corredor levantado, autorização por uso, compatibilidade de safra/frota/espaçamento e verificação da drenagem. Fazendas não adjacentes entram no roteamento logístico, não em uma única linha de sulcação.

Há evidência publicada de potencial operacional para `direct shot` ou `long shot`: no caso analisado por Santoro, Soler e Cherri, a otimização reduziu 31,64% do tempo de manobras. O produto utilizará essa evidência para definir métricas, nunca como promessa de ganho ou tempo padrão. [Santoro, Soler e Cherri, 2017](https://repositorio.unesp.br/server/api/core/bitstreams/c67d5b0e-cd67-48b8-9fec-02d2461d0d23/content)

### 6.8 Barreiras e POA/logística

O portfólio conservacionista C1-C4 e os modos OC0-OC3 serão avaliados sobre uma área já recortada por restrições. Na V1, o usuário pode enviar `power_line_axis`, um `LineString` pelo centro dos postes. Seu buffer versionado é barreira absoluta: quebra sulco, tiro, alcance hidráulico e rota; proíbe portal, POA, fila, carregamento, descarga e manobra. Sem shape, é necessária declaração `DECLARED_NONE`; ausência de arquivo não prova ausência de rede.

Depois dos gates, cada alternativa participa de quatro cenários de POA:

| ID | Conteúdo |
|---|---|
| `P0_POA_EXISTENTE` | baseline com pátios e vias existentes |
| `P1_SEM_NOVA_OBRA` | reorganiza atribuição, sequência e despacho na infraestrutura atual |
| `P2_NOVOS_POAS` | permite novos pátios e acessos elegíveis |
| `P3_INTEGRADO` | realimenta sulcação, saídas, carreadores e POAs em conjunto |

O POA é um pátio/polígono com acesso, baias, fila, giro, superfície, drenagem e capacidade. O motor calcula massa ao longo do tiro, pontos P10/P50/P90 de enchimento, janelas seguras de troca, rotas carregadas/vazias, filas e regularidade de entrega. Tiro mais longo não é automaticamente melhor quando aumenta a distância carregada, o pisoteio ou a espera da colhedora.

Tiro e POA são **objetivos posteriores aos gates**. O motor não pode alongar uma linha, conectar talhões ou escolher um pátio para recuperar pontos perdidos por violação hidráulica, elétrica, ambiental, cinemática ou de capacidade de suporte do solo. A localização usa distância na rede dirigível e capacidade/filas, nunca distância euclidiana isolada.

A lógica detalhada está em [POA e logística de colheita](./POA_E_LOGISTICA_DE_COLHEITA.md).

## 7. Gates antes da pontuação

### Gates do projeto

- MDT e referência espacial compatíveis com o nível da entrega;
- cobertura integral da área operável pelo MDT, sem extrapolação, recorte fora da extensão ou célula `NoData`;
- bacia contribuinte, entradas externas e jusante conhecidos;
- solo, infiltração, erodibilidade, compactação e cobertura;
- chuva de projeto e condições antecedentes;
- estruturas, estradas, carreadores, bueiros e receptores classificados;
- frota completa, não apenas o trator-guia;
- bitolas, articulações, pneus, cargas por eixo e envelopes por estado vazio/carregado;
- curva de capacidade de suporte/precompressão do solo por umidade, ou regra conservadora aceita pelo responsável;
- limites de fazenda/unidade legal e permissões de trânsito, trabalho, obra e água;
- perfil 3D e drenagem de cada portal entre talhões;
- declaração completa de interferências e barreira elétrica aplicada ou ausência declarada;
- produtividade, rede dirigível e frota CTT quando houver dimensionamento de POA;
- regras regionais aprovadas e vistoria.

### Gates do candidato

- cobertura da área útil e espaçamento dentro da tolerância;
- nenhuma interseção indevida ou fragmento impraticável;
- raio, curvatura, rampa, rolagem e cabeceira válidos para toda a frota;
- raio estático de trabalho e raio de manobra validados separadamente;
- envelope varrido/offtracking da última unidade dentro do corredor permitido;
- tráfego concentrado nas faixas previstas, sem invasão da linha/soqueira;
- tensão aplicada compatível com a capacidade de suporte no cenário de umidade; caso úmido reprovado fecha rota, manobra e POA;
- nenhum contragreide ou depressão sem tratamento;
- cada endpoint com estado operacional e hidráulico definido;
- capacidade, velocidade, tensão e extravasamento dentro das regras;
- nenhuma descarga desprotegida em estrada, APP, vizinho ou infraestrutura;
- nenhuma linha, rota, troca, manobra ou POA dentro da faixa elétrica V1;
- local e acesso do POA compatíveis com solo, drenagem, giro, carga e permissões;
- capacidade de transbordos, baias e caminhões válida sob os casos adversos solicitados;
- massa conservada e topologia sem nós mortos.

Uma violação hidráulica não pode ser compensada por maior rendimento numa média ponderada.

A ordem de decisão é obrigatória: `(1)` qualidade dos insumos e permissões; `(2)` conservação, hidráulica, ambiente e segurança; `(3)` dirigibilidade e solo trafegável da frota completa; `(4)` simulação colhedora-transbordos-vias-POA; `(5)` Pareto de tiros, capacidade, custo e robustez. Um cenário eliminado nas etapas 1 a 3 não recebe pontuação operacional.

No E0 atual, o espaçamento observado é apenas um diagnóstico de **menor distância euclidiana amostrada** entre linhas vizinhas. Ele não é distância normal entre sulcos e não funciona como hard gate. O gate físico de espaçamento exige um método normal/ortogonal validado e pertence a E1 ou superior.

Também são parâmetros distintos: `fleet.minimum_work_path_radius_m` limita a curvatura estática da linha trabalhada, enquanto `fleet.minimum_turn_radius_m` pertence à manobra e ao envelope cinemático na cabeceira. Um não pode substituir o outro. Sem o primeiro e sem a frota completa, o raio do E0 permanece `NOT_EVALUATED_MISSING_STATIC_PATH_RADIUS_REQUIREMENT`.

A largura de cabeceira não é um número fixo do projeto. Ela é derivada do tipo de manobra permitido (`U`, `Omega`, `T`, `P` ou outro modelo versionado), dos raios, articulações, balanços e envelopes vazio/carregado. O piloto automático do trator não valida a última carreta: o ensaio de [Passalaqua e Molin (2020)](https://doi.org/10.1590/1809-4430-Eng.Agric.v40n2p223-231/2020) demonstrou aumento do erro de trajetória ao longo do conjunto articulado, sobretudo em curva e declive lateral.

O gate de solo é dinâmico. Para cada caso de umidade e carga, a tensão transmitida deve permanecer abaixo do critério de capacidade adotado; restrição por solo úmido pode fechar temporariamente rotas, cabeceiras e POAs. Tráfego controlado significa compatibilizar espaçamento e bitola de **todos** os eixos e reboques, não apenas registrar a guia GNSS. Evidência experimental em cana sustenta o benefício dessa compatibilidade para a qualidade física e o sistema radicular ([Souza et al., 2015](https://doi.org/10.1590/0103-9016-2014-0078)).

## 8. Métricas de comparação

### Geometria e cobertura

- quilômetros de sulco e área efetivamente coberta;
- espaçamento físico normal P05/P50/P95, falhas e sobreposições em E1+; no E0, somente menor distância euclidiana amostrada;
- comprimento P10/P50/P90 dos segmentos;
- linhas curtas, morredores, fragmentos e endpoints internos;
- raio mínimo, P05 do raio, curvatura e mudança de curvatura.
- raio mínimo de trabalho e raio mínimo de manobra reportados separadamente;
- largura necessária por tipo de manobra e folga do envelope completo;

### Relevo e conservação

- greide longitudinal local e por alcance;
- inclinação transversal;
- comprimento acima de cada faixa de regra;
- reversões, depressões e volume represado;
- área contribuinte, convergência e descarga por receptor;
- vazão, velocidade, tensão, capacidade e bordo livre;
- suscetibilidade e perda de solo quando houver os fatores necessários.

### Colheitabilidade e performance

- manobras por tipo e tempo improdutivo;
- envelope varrido e invasão das linhas de cana;
- distância trabalhada e improdutiva;
- capacidade efetiva, eficiência de campo e combustível;
- massa esperada por tiro e sincronização com transbordos;
- área única trafegada e intensidade de passadas.
- comprimento físico trabalhado, tiro operacional e span total separados;
- proporção `WORK / (WORK + LIFT + CROSS)`;
- lifts, crossings, portais pendentes e economia frente ao baseline isolado;
- massa P10/P50/P90 por tiro, eventos de enchimento e janelas seguras de troca;
- quilômetros vazio/carregado por tonelada e distância roteável até o POA;
- ciclo, fila e espera de colhedora, transbordo e caminhão;
- capacidade/utilização das baias e regularidade da entrega horária;
- intensidade de tráfego, passadas repetidas e área de cultura/soqueira invadida;
- margem entre tensão aplicada e capacidade de suporte por cenário de umidade;
- offtracking máximo e P95 por eixo/reboque, trecho e estado de carga;

Tiros maiores tendem a melhorar capacidade e consumo, mas não podem ser maximizados isoladamente; ensaio com talhões de 100 a 1.900 m confirmou esse efeito e também mediu esperas, manobras e deslocamentos improdutivos. [Ramos et al., 2016](https://revistas.fca.unesp.br/index.php/energia/article/view/2102)

A linha da colhedora também não basta para validar o tráfego: conjuntos articulados apresentam erro crescente nas carretas, especialmente em curvas e declive lateral. [Passalaqua e Molin, 2020](https://repositorio.usp.br/item/002996222)

## 9. Níveis de entrega

| Nível | Insumos mínimos | Saída permitida |
|---|---|---|
| `E0_TRIAGEM` | polígono + MDT/LAZ utilizável | candidatos geométricos, perfis e métricas topográficas; POA apenas topográfico |
| `E1_OPERACIONAL` | E0 + interferências declaradas + frota + carreadores | famílias dirigíveis, cabeceiras, quebras, manobras e performance simulada |
| `E2_CONSERVACIONISTA` | E1 + bacia + solo + chuva + estruturas + receptores + logística | PCE/PCX iterados, POAs/rotas condicionados e Pareto conjunto |
| `E3_EXECUTIVO` | E2 + checkpoints + campo + parâmetros aprovados + responsável | locação, memorial, plano logístico, exportação controlada e liberação profissional |

O sistema deve impedir que uma geometria `E0` seja exportada como orientação de máquina.

## 10. Experimento E0 reproduzivel

### 10.1 Famílias geométricas dentro dos talhões

Foi implementado [generate_sulcation_scenarios.py](../scripts/generate_sulcation_scenarios.py) para executar um sweep parametrizavel de orientacoes e intensidades de deformacao pelo relevo. Os seis resultados publicados são **primitivas geométricas**, não substituem os macrocenários C1-C4. Nas próximas etapas, cada macrocenário utilizará as primitivas compatíveis e acrescentará estruturas, superfície proposta, hidráulica e operação.

Premissas apenas de estudo:

- espaçamento de 1,50 m;
- cabeceira externa de 12 m;
- afastamento configuravel de ilhas ainda nao classificadas;
- segmento mínimo de 8 m;
- greide-alvo ilustrativo de 2%;
- alerta topográfico ilustrativo de 5%;
- seis estratégias exploratórias globais; nenhuma fronteira analítica cria uma quebra de trabalho.

Os resultados devem registrar por estrategia o comprimento total, quantidade de
segmentos, distribuicao de greide, reversoes, espacamento e motivos de bloqueio.
Uma direcao unica por talhao e apenas baseline; ranking relativo nunca aprova
uma alternativa que falhe um gate absoluto.

**Ajuste adaptativo médio dos níveis de fase.** A primeira versão usava passo fixo de `row_spacing_m` em unidades de `phi`, embora `phi = normal·(x,y) + lambda·z` só tenha `||grad(phi)|| = 1` nas retas. A heurística atual escolhe cada `delta_phi` pela mediana de `||grad(phi)||` numa faixa e corrige a densidade média. Ela não normaliza o campo e não garante o espacamento fisico ao longo de toda isolinha: um único `delta_phi` não corrige variação do gradiente sobre a mesma curva. O diagnóstico publicado mede a menor distância euclidiana em pontos amostrados entre linhas vizinhas; não mede distância normal e não elimina candidatos em E0. Campo de distância/eikonal, offsets de curva-semente ou relaxamento geométrico, seguidos de validação normal, continuam necessários para uma garantia física.

Artefatos:

- `dataset/derived/sulcation_scenarios.gpkg` (local);
- `dataset/derived/sulcation_scenarios_map.png` (local);
- `dataset/derived/sulcation_scenario_metrics.json` (local);
- [verificador dos resultados](../scripts/verify_sulcation_scenarios.py).

### 10.1.1 Continuidade operacional sem manobra interna

A experiência de dividir o talhão em zonas independentes foi retirada do produto. Ela criava términos e reinícios sobre uma fronteira apenas analítica, onde não existe carreador, cabeceira ou área física para a plantadora virar. A tentativa posterior de fazer média direta dos campos `phi` também foi rejeitada: campos escalares têm fase arbitrária, orientação de linha é axial (`theta` e `theta + 180` são equivalentes) e a média pode cancelar gradientes, gerar singularidades e depender da origem UTM.

O módulo [operational_continuity.py](../scripts/operational_continuity.py) agora aplica, antes da publicação, as seguintes invariantes:

1. fronteira topográfica nunca é superfície de parada;
2. todo endpoint deve alcançar a borda da área de trabalho, a borda de um obstáculo físico ou outra superfície operacional cadastrada;
3. cruzamentos, sobreposições, toques entre sulcos, auto-interseções e loops fechados sem entrada aprovada reprovam a geometria;
4. cabeceira E0 suporta apenas o término geométrico; manobra continua `NOT_EVALUATED_MISSING_FLEET_ENVELOPE`;
5. obstáculo ou rede elétrica exige quebra, mas não autoriza manobra ou travessia;
6. cada trecho permanece `ISOLATED_SEGMENT_E0`; um futuro tiro operacional só poderá agrupar trechos após resolver `WORK/LIFT/CROSS/TURN`.

O raio estático da linha e o raio de manobra também permanecem separados: `fleet.minimum_work_path_radius_m` será o gate da geometria trabalhada em E1+, e `fleet.minimum_turn_radius_m` será aplicado ao movimento e ao envelope na cabeceira. Como o E0 não recebeu o requisito estático nem prova a frota, seu estado é `NOT_EVALUATED_MISSING_STATIC_PATH_RADIUS_REQUIREMENT`, mesmo quando a curvatura observada é persistida para diagnóstico.

O verificador reprova endpoints internos sem suporte, falhas de continuidade e
geometrias invalidas. Pontas que alcancem cabeceira presumida ou obstaculo ainda
nao provam manobra: o inventario de interferencias e a frota sao obrigatorios
antes de qualquer linha operacional.

### 10.2 Triagem de continuidade entre os dois talhões

Foi implementado também [analyze_multifield_connections.py](../scripts/analyze_multifield_connections.py). O ensaio lê diretamente os polígonos, o MDT e as linhas E0. Adjacência geométrica não prova carreador, portal, largura, drenagem ou autorização; por isso todos os resultados são `UNCONFIRMED`.

Se não existir `power_line_axis` nem declaração de ausência, nenhum conector pode ser promovido. Uma nuvem que preserve apenas pontos de solo não confirma postes ou cabos; portanto nenhum conector geométrico pode atravessar a interface em um produto operacional antes dessa verificação.

O comparativo registra pares geometricos, gap, greide local, reducao potencial de
grupos e break-even simplificado. O `E0F` preserva um unico eixo reto e fase
global; o `E0G` testa orientacoes proximas a normal local da divisa e varias
fases. A melhor combinacao da amostra nao e um otimo global.

O tempo de manobra evitada e uma premissa configuravel da comparação multi-talhão, não um tempo padrão da frota nem crédito automático para as famílias dentro de cada talhão. O gerador intratalhão penaliza cada segmento físico isolado como uma nova operação e registra `E0_ISOLATED_SEGMENT_PENALTY_NO_ROUTE_CREDIT`; somente um grafo validado poderá conceder economia por rota contínua. Portanto o resultado comprova **potencial de otimização de direção e fase**, não aprova o tiro nem recomenda uma orientação.

O próximo solver precisa manter o pareamento do portal enquanto deforma a família por preferências locais em um domínio contínuo, sem criar endpoints analíticos, além de reduzir greide, respeitar cada sistema conservacionista e testar o envelope da frota. A memória e o GeoPackage de cada execução permanecem em `dataset/derived/`, fora do repositório público, sempre marcados `UNCONFIRMED` em E0.

## 11. Próximos incrementos do motor

### Incremento 0: contrato e restrições

- resolver o pedido de geração pelo catálogo versionado de parâmetros;
- consumir o manifesto no lugar das constantes E0 duplicadas;
- exigir declaração completa de interferências;
- recortar área, sulcos, alcances e rotas pelo `power_line_axis`;
- retornar `NO_FEASIBLE_CANDIDATE` em E1+ quando todos os candidatos falharem.

### Incremento 1: famílias híbridas com preferências locais

- [x] implementar ajuste adaptativo médio de níveis e medir o espaçamento diretamente; normalização física continua pendente;
- [x] proibir endpoints internos e separar término geométrico de autorização de manobra;
- [x] persistir nós operacionais, status de raio, blockers e superfícies de término;
- [ ] gerar preferências direcionais locais como orientação axial, sem recortar o domínio nem criar endpoints;
- [ ] resolver um único campo de fase integrável, ancorado e aproximadamente unitário;
- [ ] hibridizar curvas-semente com raio mínimo e envelope da plantadora como hard gates;
- [ ] medir diversidade e remover cenários equivalentes.

### Incremento 2: frota e cabeceiras

- modelo cinemático por veículo e implemento;
- envelopes varridos dos transbordos;
- transições de curvatura contínua;
- grafo de talhões e portais autorizados;
- fase global e pareamento monotônico de endpoints;
- estados `WORK`, `LIFT`, `CROSS`, `TURN` e `TRANSIT`;
- manobras e rotas entre segmentos;
- massa por tiro, eventos de enchimento e janelas de troca.

### Incremento 3: sistema conservacionista

- bacia completa e rede de drenagem;
- PCE e PCX como cálculos separados e iterativos;
- famílias terraceada, drenada, ESD e mista;
- simulação nominal e pior caso;
- receptores e caminhos de falha explícitos.

### Incremento 4: POA e logística

- grafo dirigível com estados vazio/carregado e restrições sazonais;
- POA como polígono capacitado, com acesso, baias, fila, giro e drenagem;
- localização, atribuição e roteamento integrados;
- despacho e simulação de filas/falhas sob P10/P50/P90;
- realimentação de direção, fase, saídas e carreadores;
- mapa de tráfego, pisoteio e compactação potencial.

### Incremento 5: calibração

- projetos históricos em DXF/DWG/GPKG;
- trajetórias executadas e as-built;
- telemetria, tempos, combustível e perdas;
- inspeções depois de chuvas;
- revisão cega com projetistas e agrônomos.

O projeto manual em AgroCAD deve entrar como candidato de benchmark, nunca como verdade-terreno nem como desenho a ser copiado.

## 12. Critério de sucesso do produto

O motor estará pronto para piloto quando conseguir, de forma reproduzível:

1. gerar famílias distintas e mecanizáveis, não apenas linhas-base;
2. explicar por que cada candidato foi eliminado ou selecionado;
3. separar continuidade operacional de continuidade hidráulica;
4. provar cobertura, espaçamento e dirigibilidade da frota inteira;
5. fechar a água desde a contribuição a montante até o receptor a jusante;
6. comparar alternativas no mesmo conjunto de dados e regras;
7. repetir o cálculo sob incerteza;
8. exportar geometrias 3D, perfis, violações e memória de cálculo auditável.
9. quebrar deterministicamente linhas e rotas em todas as barreiras declaradas.
10. localizar e dimensionar POAs sem aumentar risco hídrico, elétrico ou de compactação.

Esse é o núcleo do produto. A plataforma visual será somente a forma de operar, revisar e aprovar esse motor.
