# Métodos Conservacionistas Regionais para Cana-de-Açúcar

**Versão:** 1.1
**Data:** 20 de agosto de 2026
**Status:** base técnica para implementação, validação agronômica e configuração regional

## 1. Objetivo e escopo

Este documento organiza as práticas mecânicas e os sistemas de manejo do escoamento superficial que o TerraFlux deve representar ao sistematizar canaviais. O objetivo não é produzir uma receita agronômica nacional, mas estabelecer:

1. Uma taxonomia que não misture função hidráulica, geometria, método construtivo e operabilidade.
2. Critérios condicionais para propor e comparar alternativas.
3. Bloqueios que impeçam dimensionamentos sem dados ou sem destino seguro para a água.
4. Um modelo de regras regional, versionado e rastreável até a fonte.
5. A separação entre cálculo automatizado e decisão de profissional habilitado.

A referência mais aderente à cultura é o [Boletim Técnico IAC 216 - Recomendações gerais para a conservação do solo na cultura da cana-de-açúcar](https://www.iac.sp.gov.br/media/publicacoes/iacbt126.pdf). Como orientação nacional recente, o [Manual do Programa Produtor de Água, volume 5, da ANA](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais/manual-programa-produtor-de-agua-volume-5) reúne práticas mecânicas, terraceamento e Escoamento Superficial Difuso. A [Embrapa](https://www.embrapa.br/en/web/agencia-de-informacao-tecnologica/cultivos/cana-de-acucar/producao/correcao-e-adubacao/manejo-e-conservacao) também descreve tipos usados em canaviais e suas limitações operacionais.

Esses documentos são referências técnicas, não substituem projeto local, legislação estadual, atribuição profissional ou vistoria. Limites publicados para São Paulo, Cerrado ou determinada cultura não devem ser promovidos a regra nacional sem validação.

A separação entre sistema ESD, sulcos, linhas de controle, carreadores, receptores e CEV está detalhada em [Lógica do ESD e do Canal Escoadouro](LOGICA_ESD_E_CANAL_ESCOADOURO.md).

## 2. Fonte, evidência e inferência

Toda regra e todo parâmetro precisam carregar uma das classificações abaixo:

| Classe | Significado | Uso permitido |
|---|---|---|
| `fonte_oficial` | Lei, regulamento, manual ou boletim de órgão público | Regra ou referência com território e vigência explícitos |
| `fonte_primaria` | Ensaio, tese, artigo ou dados experimentais originais | Calibração, intervalo observado e evidência de risco |
| `pratica_setorial` | Procedimento utilizado por usina, cooperativa ou consultoria | Cenário a validar; não é norma |
| `inferencia_produto` | Regra de segurança ou modelagem proposta pelo TerraFlux | Deve ser identificada como inferência e aceita pelo responsável técnico |
| `exemplo_fonte` | Valor ilustrativo presente numa publicação | Nunca vira limite automático |

O sistema deve mostrar, para cada decisão, o texto da regra, valor, unidade, fonte, página ou seção, jurisdição, data, versão, responsável pela seleção e eventual justificativa de exceção.

## 3. Taxonomia multidimensional

“Tipo de curva” não é um campo técnico suficiente. Uma mesma obra possui atributos independentes.

| Eixo | Valores principais | Pergunta respondida |
|---|---|---|
| Função hidráulica | TI, TD, mista, difusa | A água será armazenada, infiltrada, conduzida ou dispersa? |
| Gradiente longitudinal | em nível, gradiente controlado, variável | Qual é a queda ao longo da estrutura? |
| Geometria | base larga, média, estreita, embutida, invertida | Qual é a seção e a faixa movimentada? |
| Método construtivo | Mangum, Nichols, Nichols invertido | Como o solo é cortado e deslocado? |
| Operabilidade | passante, não passante, travessia localizada | Máquinas podem cruzar a estrutura? |
| Receptor | infiltração local, TD, CEV, dissipador, drenagem protegida | Onde termina a água excedente? |
| Sistema | terraceado, híbrido, ST, ESD | Como as estruturas e o manejo funcionam em conjunto? |

### 3.1 Termos que não são sinônimos

- **Curva de nível** pode significar uma isolinha cartográfica, um alinhamento de plantio próximo ao nível ou, no uso coloquial, um terraço. O banco de dados deve proibir essa ambiguidade.
- **Em nível** descreve o gradiente longitudinal, não a forma da seção.
- **Base larga** descreve geometria; não informa se a estrutura infiltra ou drena.
- **Passante** descreve cruzamento por máquinas; não informa função hidráulica.
- **Embutido** descreve seção e construção; pode integrar um sistema de infiltração ou drenagem conforme o projeto.
- **Canal tipo terraço** e **canal escoadouro vegetado** são objetos distintos. O primeiro intercepta uma faixa de vertente; o segundo recebe e transporta contribuições de uma microbacia.
- **ESD** é um sistema de manejo e projeto, não uma estrutura isolada.

### 3.2 Termos regionais

**Inferência terminológica:** “curva embutida” é o nome operacional usado no setor para “terraço embutido”. As referências técnicas oficiais pesquisadas preferem “terraço embutido”; o uso setorial de “curva embutida” e “terraço de base larga passante” aparece, por exemplo, em material técnico da [Coopercitrus](https://revistacoopercitrus.com.br/artigo-tecnico-o-plantio-da-cana-de-acucar/). O produto pode aceitar o sinônimo na interface, mas deve persistir o termo técnico normalizado.

“Terraço em gradiente”, “em desnível” e “de drenagem” são usados como equivalentes operacionais quando a estrutura possui saída aberta e conduz água. A classificação persistida deve ser `TD`, acompanhada do gradiente longitudinal efetivamente projetado.

### 3.3 Nomes apresentados na carteira de cenários

| Nome apresentado ao cliente | Configuração técnica permitida |
|---|---|
| `Curva embutida` | seção `EMBUTIDA`, função `TI` ou `TD`, normalmente não passante, sulcação gerada por faixa entre terraços |
| `Base larga/passante` | seção `BASE_LARGA`, função `TI` ou `TD`, passagens validadas por estação e seção degradada pelo tráfego simulada |
| `ESD` | sistema `ESD`, sulcos com greide controlado e papel hidráulico, variante setorial ou complementada |
| `Misto por zonas` | duas ou mais configurações anteriores, com transferências hidráulicas e operacionais explícitas |

Esses nomes são macrocenários de comparação, não substituem a taxonomia multidimensional. “Curva embutida” não define por si só TI ou TD; “passante” não libera qualquer ponto de cruzamento; e uma área sem terraços não recebe automaticamente o nome ESD.

## 4. PCE e PCX como estrutura do projeto

O IAC organiza o planejamento em dois componentes complementares:

- **PCE, Projeto de Controle da Erosão:** risco de desagregação e transporte do solo; cobertura, preparo, sulcação, espaçamento, cultura, época de reforma e práticas vegetativas ou edáficas.
- **PCX, Projeto de Controle da Enxurrada:** volume e vazão; estruturas de armazenamento, condução, recepção, dissipação e manutenção.

Nenhum método deve ser recomendado isoladamente. Um desenho pode cumprir o espaçamento do PCE e ainda falhar hidraulicamente por seção insuficiente, contribuição externa ou descarga insegura. O TerraFlux deve calcular e validar PCE e PCX separadamente, depois verificar sua compatibilidade.

A RUSLE/RUSLE2 pode apoiar o PCE estimando perda média de solo por erosão laminar e em sulcos sob combinações de erosividade, erodibilidade, relevo, cobertura e prática. Ela **não substitui o PCX**: não dimensiona vazão de pico, armazenamento, remanso, CEV, bueiro, dissipador ou consequência de uma descarga concentrada. O próprio [USDA-NRCS](https://www.nrcs.usda.gov/sites/default/files/2022-10/National-Agronomy-Manual.pdf) delimita a tecnologia à erosão laminar e em sulcos e separa canais concentrados e voçorocas; o cálculo hidráulico continua obrigatório.

## 5. Terraço de Infiltração - TI

### 5.1 Definição

O TI é construído em nível, normalmente com pontas fechadas, e armazena a enxurrada até sua infiltração. Sua viabilidade depende da capacidade hidráulica do perfil, não apenas da textura superficial.

Na classificação do IAC para cana em São Paulo:

- Grupos de solo 1 e 2, mais profundos e permeáveis, podem admitir TI ou TD.
- Grupos 3 e 4, com perfil restritivo, permeabilidade baixa ou menor profundidade, recebem recomendação de TD.

O IAC ressalta que alguns Argissolos de superfície arenosa possuem gradiente textural abrupto, baixa infiltração no perfil e maior formação de enxurrada. Portanto, a regra “arenoso infiltra bem” é tecnicamente inválida.

### 5.2 Dimensionamento e verificações

A referência do IAC calcula o volume de enxurrada por `V = A × h × c`, em que `A` é a área contribuinte, `h` é a chuva máxima adotada e `c` é o coeficiente de enxurrada associado a relevo, solo, cobertura, preparo e manejo. A seção útil precisa armazenar esse volume.

O cálculo não termina na capacidade geométrica. Devem ser verificados:

1. Tempo de esvaziamento antes de nova chuva relevante.
2. Umidade antecedente e eventos consecutivos.
3. Compactação do canal por construção e tráfego.
4. Selamento por sedimentos.
5. Lençol raso, surgências e impedimentos no perfil.
6. Cota da crista, pontos baixos, bordo livre e consequência do extravasamento.
7. Contribuições de estradas, bueiros, vizinhos e terraços a montante.

Uma [tese primária da USP/ESALQ](https://teses.usp.br/teses/disponiveis/11/11140/tde-21012019-150102/pt-br.html) encontrou grande variação espacial e temporal das taxas de infiltração em canais e registrou falhas hidráulicas após sequências de chuva. O estudo não fornece um coeficiente universal, mas demonstra que classe pedológica ou textura isolada não garantem o funcionamento do TI.

### 5.3 Bloqueios

O TerraFlux deve bloquear a liberação de TI quando houver:

- perfil do solo ou permeabilidade desconhecidos;
- grupo equivalente a 3 ou 4 sem justificativa técnica específica;
- incapacidade de armazenar a chuva de projeto;
- tempo de esvaziamento incompatível com o cenário de chuvas sucessivas;
- surgência, alagamento persistente ou lençol limitante;
- extravasamento direcionado a área sem proteção;
- ausência de plano de inspeção e manutenção.

## 6. Terraço de Drenagem - TD

### 6.1 Definição

O TD, também chamado de terraço em gradiente ou desnível, intercepta a enxurrada e a conduz a uma rede ou estrutura de descarga segura. Pode ter ponta inferior aberta ou, quando curto e em nível, uma ou duas pontas abertas. Não é permitido desenhar TD sem receptor dimensionado. No `rule_pack BR-SP-CANA-IAC216`, a saída deve conectar a CEV ou prado escoadouro **implantado e estabilizado antes** do TD; a regra nacional mais geral de “receptor estável” não enfraquece essa sequência paulista.

### 6.2 Dimensionamento

O IAC utiliza a fórmula racional para a vazão de pico:

`Q = c × i × A / 360`

em que `Q` está em metros cúbicos por segundo, `A` em hectares, `i` corresponde à intensidade da chuva no tempo de concentração e `c` representa a fração de escoamento. A seção é verificada como canal aberto e deve respeitar capacidade, bordo livre, estabilidade e velocidade admissível.

O boletim paulista registra, como referências de sua metodologia, comprimentos de 500 a 600 m, gradiente longitudinal comum de 3/1000, podendo chegar a 7/1000, e velocidades de 0,60 a 0,75 m/s. Esses números são **referências regionais e metodológicas**, não defaults nacionais. O rule pack deve vinculá-los a `BR-SP/IAC-BT216-2016` e exigir recálculo para o local, solo, seção, revestimento, sedimento e chuva escolhidos.

O período de retorno de 10 anos aparece como referência no IAC. **Inferência de produto:** o responsável técnico deve poder adotar valor maior conforme consequência da falha, ativos a jusante e critérios atuais, usando IDF local vigente. Mapas históricos incorporados ao boletim não substituem atualização climática ou pluviométrica.

### 6.3 Bloqueios

- ausência de IDF ou chuva de projeto aceita pelo responsável;
- tempo de concentração não calculado;
- microbacia recortada no limite do talhão;
- velocidade acima da admissível para solo e cobertura;
- deposição/assoreamento provável sem solução de manutenção;
- descarga em estrada, talude, APP, imóvel vizinho ou curso d’água sem verificação;
- CEV ou receptor ainda não estabilizado.

## 7. Base larga e passante

O terraço de base larga possui canal largo e raso e camalhão suavizado. O IAC o associa normalmente ao método Mangum, com movimentação de terra de ambos os lados. Permite maior aproveitamento da área e se ajusta melhor à mecanização em terrenos de menor declive.

“Passante” é a condição de um terraço de base larga que permite trânsito por cima do camalhão. A passagem não elimina a estrutura: o IAC exige seção mínima calculada no PCX.

A Embrapa cita o terraço de base larga para cana até aproximadamente 6% de declividade. Esse valor deve ser armazenado como recomendação de fonte, com advertência, e não como limite automático de aprovação. A declividade da encosta também não deve ser confundida com o pequeno gradiente longitudinal de um TD.

### 7.1 Benefícios

- plantio e colheita sobre maior parte da seção;
- menor fragmentação operacional;
- cruzamento de máquinas;
- potencial redução de manobras e área improdutiva.

### 7.2 Riscos

- rebaixamento do camalhão e perda de seção;
- compactação por tráfego;
- trilhas que orientam e concentram o escoamento;
- erosão iniciada no cruzamento, especialmente em solos arenosos desagregáveis;
- conflito entre orientação das linhas, menor ângulo de interceptação e raio de giro;
- falsa percepção de capacidade por a estrutura ser suave.

O cenário passante deve simular a seção construída e a seção degradada após tráfego. Deve também exigir inspeção posterior a chuvas intensas e verificação das entradas, saídas, crista e cruzamentos.

## 8. Embutido e embutido invertido

### 8.1 Terraço embutido

O embutido movimenta uma faixa menor em maior profundidade, normalmente pelo método Nichols, cortando e deslocando a terra de montante para jusante. Forma canal mais profundo, camalhão mais abrupto e estrutura mais resistente, mas geralmente não permite plantio integral nem cruzamento livre.

Critérios e impactos a representar:

- maior adequação relativa a situações em que base larga não é operacionalmente ou estruturalmente satisfatória;
- área não plantável e perda de eficiência operacional;
- volume de corte e aterro;
- talude abrupto e pontos de travessia;
- exposição de subsolo e necessidade de correção química e orgânica no canal;
- risco de extravasamento em cascata para estruturas a jusante.

### 8.2 Terraço embutido invertido

No invertido, o solo é deslocado de jusante para montante, criando talude posterior abrupto. O IAC o apresenta para declividades intermediárias de 10% a 18% dentro de sua recomendação paulista e registra melhor aproveitamento para plantio, acompanhado de movimentação intensiva de solo e necessidade de correção da faixa.

O risco crítico é o turbilhonamento e solapamento do talude quando ocorre extravasamento, especialmente em solos médios e arenosos. A faixa de 10% a 18% não deve ser nacionalizada; deve funcionar como regra regional de triagem e sempre exigir cálculo hidráulico, análise de solo, consequência de falha e aprovação profissional.

## 9. Canal Escoadouro Vegetado - CEV

O CEV recebe e conduz excessos de TD, estradas, sistemas sem terraço ou pontos de convergência. Segundo o IAC, deve ser largo, raso, de pequena declividade e leito estável, preferencialmente alocado em depressão natural. Sua seção pode ser triangular, trapezoidal ou parabólica.

O objeto pode ter origem `NATURAL`, quando um prado/depressão existente possui cobertura e estabilidade demonstradas para a vazão, ou `CONSTRUCTED`, quando o canal e sua proteção foram implantados. “Natural” não significa “dispensado de cálculo”: ambos exigem microbacia completa, seção, velocidade/tensão admissível, descarga final, inspeção e manutenção. Talvegue, curso d'água ou APP não recebe automaticamente a classificação de CEV.

O dimensionamento deve seguir a sequência:

1. Localizar canal, seções de controle e descarga final.
2. Delimitar toda a microbacia contribuinte.
3. Caracterizar solo do canal e áreas contribuintes.
4. Medir o gradiente.
5. Escolher cobertura adaptada e determinar rugosidade.
6. Calcular vazão de projeto.
7. Definir velocidade admissível sem erosão.
8. Dimensionar área, largura, profundidade e bordo livre.
9. Repetir seções onde contribuição, solo ou gradiente mudarem.
10. Verificar descarga, dissipação e efeitos a jusante.

O CEV deve ser implantado e estabilizado **antes** de receber terraços de drenagem e, no pack paulista, antes da liberação de ST. Tráfego no leito deve ser evitado; cobertura, entradas, saídas, assoreamento e transbordamento exigem manutenção. Se a velocidade admissível não puder ser alcançada, o projeto precisa incorporar proteção, dissipadores ou estruturas de controle de nível.

### 9.1 Terraço “canal” não é CEV

A Embrapa também chama de “canal” um tipo de terraço usado em solo com deficiência de drenagem e maior risco de assoreamento. Para impedir erro de projeto, o modelo deve separar:

- `terrace.cross_section = channel`, estrutura interceptora;
- `waterway.type = CEV`, receptor da microbacia.

Água subsuperficial, nascente ou lençol raso não é resolvida automaticamente por nenhuma dessas estruturas. **Inferência de produto:** esses sinais devem abrir uma investigação de drenagem específica e bloquear recomendações baseadas apenas em escoamento superficial.

## 10. Sulcação em nível e em gradiente controlado

O IAC relata efeito significativo da sulcação em nível no controle da erosão em declividades de 3% a 12%, podendo chegar a 18% em solos mais resistentes. Esses intervalos são referências paulistas condicionadas ao solo e não autorização automática.

Tiros retos em declives maiores podem funcionar como plantio morro abaixo quando os sulcos conduzem água de modo descontrolado. Por isso devem ser restritos a terrenos mais planos, perfis infiltrantes e solos resistentes à desagregação.

Cada linha proposta deve ser verificada quanto a:

- gradiente longitudinal máximo, médio e percentis;
- trechos reversos e depressões que acumulam água;
- comprimento hidráulico contínuo;
- área que passa a contribuir para o sulco;
- confluências entre linhas, carreadores e terraços;
- velocidade e tensão de cisalhamento estimadas;
- ângulo de encontro com terraços;
- raio de curvatura, espaçamento, cabeceira e capacidade da frota;
- destino da água no fim da linha.

## 11. Escoamento Superficial Difuso - ESD

### 11.1 Significado e posição técnica

ESD significa **Escoamento Superficial Difuso**. O manual da ANA o descreve como técnica de manejo de solo e água que busca dispersar homogeneamente o fluxo superficial, usando rugosidade e plantio em gradiente controlado para reduzir velocidade, retenção localizada e erosão. Na prática canavieira descrita pela literatura, a enxurrada é distribuída por muitos sulcos rasos, e não concentrada em uma única linha denominada ESD.

Sua adoção regional recente em Mato Grosso do Sul aparece em comunicação oficial da [Agraer/Semadesc](https://www.agraer.ms.gov.br/tecnicos-da-agraer-participam-de-capacitacao-sobre-tecnica-de-escoamento-superficial-difuso-para-conservacao-de-solo-e-agua/), apresentada como estratégia complementar às ações do Prosolo. Isso demonstra uso institucional e regional, mas não constitui norma nacional de dimensionamento.

O exemplo do manual da ANA com linhas a 2% numa vertente de 10% é um caso ilustrativo. **Inferência obrigatória para o produto:** 2% não deve ser transformado em limite universal de sulcação.

### 11.2 O que ESD não é

- Não é plantio morro abaixo.
- Não é simplesmente eliminar terraços.
- Não é alongar tiros retos para ganhar rendimento.
- Não é sinônimo de CEV nem de canal escoadouro.
- Não elimina CEV, TD, dissipadores ou outras estruturas quando o PCX demonstra sua necessidade.
- Não pode ser definido apenas pela orientação das linhas no MDT.
- Não pode ser identificado pela cor de uma camada CAD.

### 11.3 Componentes e lógica hidráulica

O sistema deve persistir como objetos distintos:

- linha-base ou linha de controle, que pode ser apenas uma referência geométrica;
- família completa de sulcos de plantio;
- alcances hidráulicos dentro de cada sulco;
- carreadores e sua drenagem própria;
- receptores naturais protegidos, CEV e estruturas complementares;
- exutórios e condições de jusante.

A cadeia calculada será `chuva -> excesso superficial -> contribuição lateral -> sulcos -> transferências -> receptor estável -> jusante`. Cada sulco funciona como pequeno conduto de uma parcela da vertente; o CEV, quando presente, funciona como receptor de vazões acumuladas e precisa de dimensionamento próprio.

Uma linha-guia pode cruzar um carreador para preservar a continuidade operacional. O caminho hidráulico, entretanto, é interrompido nesse ponto, salvo quando houver uma travessia ou transferência explicitamente projetada. Carreador não será tratado automaticamente como canal ou saída.

### 11.4 Condições mínimas

Um cenário ESD deve incluir:

- PCE e PCX completos;
- modelo da bacia inteira, inclusive estradas e contribuições externas;
- perfil hidráulico do solo e compactação;
- rugosidade e cobertura por fase do ciclo;
- gradiente controlado das linhas e ausência de concentrações perigosas;
- caminhos de excesso e receptores dimensionados;
- implantação faseada, quando necessária, para não expor toda a área;
- monitoramento contínuo e revisão após eventos intensos.

Em áreas compactadas, pouco permeáveis, íngremes ou com convergência acentuada, ESD isolado pode ser insuficiente. O cenário pode incluir TD, CEV, faixas vegetadas, dissipação e proteção de estradas somente como componentes explícitos, dimensionados e recalculados na mesma rede.

O gate universal é: **todo alcance hidráulico termina em receptor estável e verificado**. O CEV é obrigatório quando a variante regional selecionada ou o PCX o exigir; não deve ser inserido como requisito cego em todo cenário ESD. Quando um componente ESD transfere água a um TD ou integra ST sob o pack IAC/SP, prevalece a obrigação regional de CEV/prado previamente estabilizado.

## 12. Sistema Sem Terraços - ST

O IAC denomina ST o sistema conservacionista que prescinde de terraceamento contínuo. O boletim o condiciona a áreas de muito baixo risco, melhoria de cobertura e infiltração, rampas curtas ou situação em que o espaçamento calculado dos terraços seja maior que a própria rampa. No pack paulista, “sem terraços” não significa “sem rede de descarga”: o ST exige CEV/prado escoadouro previamente estabilizado e as demais estruturas apontadas pelo PCX.

Mesmo sem terraços, o PCX é imprescindível. O sistema deve prever estruturas a jusante, drenagem de estradas, plantio cortando o fluxo, faixas vegetadas, rugosidade ou canais divergentes. Os sulcos não podem concentrar enxurrada nem possuir declividade elevada, especialmente em solos arenosos.

O IAC reconhece poucos dados de erosão, enxurrada, infiltração e sedimentos em sistemas sem terraceamento e condiciona desvios de suas recomendações ao tratamento como inovação tecnológica em São Paulo.

### 12.1 Relação entre ESD e ST

**Inferência de modelagem:** ESD e ST não devem ser persistidos como sinônimos. `ST` informa ausência de terraceamento sistemático; `ESD` informa a estratégia de distribuir o escoamento por rugosidade e alinhamentos controlados. Uma área sem terraços e com tiros longos não é automaticamente ESD. Quando estruturas complementares forem necessárias, o cenário será classificado como `ESD_COMPLEMENTADO`, com cada TD, CEV, dissipador e transição explicitamente calculado, e não como uma combinação informal de métodos.

## 13. Critérios condicionais de seleção

| Condição dominante | Alternativas a avaliar | Restrições principais |
|---|---|---|
| Perfil profundo e permeável, baixa convergência | TI, base larga TI, híbrido | Confirmar esvaziamento e chuvas consecutivas |
| Perfil restritivo, gradiente textural, drenagem lenta | TD + CEV | Receptor estabilizado e velocidade admissível |
| Declive suave e alta necessidade de cruzamento | Base larga passante | Seção pós-tráfego, compactação e orientação dos sulcos |
| Terreno em que base larga perde segurança/viabilidade | Embutido ou solução híbrida | Área perdida, correção do canal e travessias |
| Declividade intermediária dentro de regra paulista aplicável | Embutido invertido como cenário | Alto risco de solapamento no extravasamento |
| Rampas muito curtas ou espaçamento calculado maior que a rampa | ST ou híbrido | PCX e estruturas de excesso continuam obrigatórios |
| Rugosidade e cobertura controláveis, sem concentração perigosa | ESD setorial ou ESD complementado | PCE/PCX, receptores estáveis, evidência local, implantação e monitoramento intensivos |
| Estradas ou contribuição externa relevante | Interceptores, TD, CEV, dissipação | Não descarregar no talhão sem dimensionar |
| Surgências, lençol raso ou alagamento persistente | Investigação de drenagem específica | Bloquear solução apenas superficial |

Essa matriz gera alternativas, não decide o projeto. A decisão final depende do conjunto solo-chuva-relevo-manejo-operação e da consequência de falha.

## 14. Variáveis de solo, chuva, relevo e manejo

### 14.1 Solo

- classe e descrição do perfil;
- profundidade efetiva;
- textura por horizonte e razão textural B/A;
- condutividade/permeabilidade e ensaio de infiltração representativo;
- agregação e erodibilidade;
- compactação por camada;
- pedregosidade e impedimentos;
- drenagem, lençol e surgências;
- condição específica do canal, que pode diferir do solo agrícola adjacente.

### 14.2 Chuva e hidrologia

- série e equação IDF local;
- duração associada ao tempo de concentração;
- período de retorno escolhido;
- chuva diária/volume para TI;
- intensidade/vazão de pico para TD e CEV;
- umidade antecedente e sequências de eventos;
- contribuição externa e descarga a jusante;
- sensibilidade a coeficientes de enxurrada.

### 14.3 Relevo

- MDT validado e resolução compatível;
- declividade da encosta separada do gradiente da estrutura;
- comprimento e forma da vertente;
- concavidades, divisores, talvegues e depressões;
- microbacias completas;
- pontos de concentração e mudança de solo;
- estabilidade do receptor.

### 14.4 Manejo e mecanização

- palha e cobertura em cada fase;
- preparo, cultivo mínimo, reforma e época de solo exposto;
- tráfego, massa por eixo e pressão de contato;
- espaçamento, bitola, raio de giro e piloto automático;
- orientação de plantio, colheita e carreadores;
- cultura de rotação;
- plano de manutenção e capacidade operacional de resposta.

A reforma do canavial é fase crítica porque pode coincidir com solo exposto e chuva torrencial. O rule pack deve variar cobertura e coeficiente de enxurrada por fase, em vez de usar uma única condição anual.

### 14.5 Colheitabilidade como gates físicos

Colheitabilidade não é um bônus capaz de compensar conservação. Antes de pontuar tiros e logística, o candidato precisa provar:

- `minimum_work_path_radius_m` na linha trabalhada e `minimum_turn_radius_m` na manobra como parâmetros diferentes;
- envelope varrido do conjunto articulado completo, inclusive a última carreta, em retas, curvas, declive lateral, portais e cabeceiras;
- rodas dentro das faixas permanentes de tráfego e fora da linha/soqueira, com bitolas compatíveis em toda a frota;
- capacidade de suporte do solo no teor de água do caso simulado; solo úmido acima do limite aceito bloqueia tráfego, POA e manobra;
- inclinações longitudinal e transversal, quebra vertical, estabilidade e altura de corte compatíveis com a frota e o fabricante.

O desvio da última carreta pode ser muito maior que o do trator, inclusive com piloto automático, como demonstrado por [Passalaqua e Molin (2020)](https://doi.org/10.1590/1809-4430-Eng.Agric.v40n2p223-231/2020). O tráfego controlado precisa compatibilizar bitola e espaçamento de todo o conjunto; ensaio em cana mostrou melhor qualidade física do solo e maior massa radicular quando essa compatibilidade foi aplicada ([Souza et al., 2015](https://doi.org/10.1590/0103-9016-2014-0078)).

## 15. Rule pack regional e versionado

### 15.1 Estrutura mínima

```yaml
rule_pack:
  id: BR-SP-CANA-IAC216
  version: 1.0.0
  jurisdiction: BR-SP
  crop: sugarcane
  source:
    title: Boletim Tecnico IAC 216
    year: 2016
    url: https://www.iac.sp.gov.br/media/publicacoes/iacbt126.pdf
  validity:
    starts_at: 2016-01-01
    reviewed_at: null
  parameters: {}
  rules: []
```

Cada regra deve possuir:

- `rule_id` e versão semântica;
- condição legível e expressão executável;
- severidade `info`, `warning`, `requires_review` ou `blocker`;
- natureza `official`, `primary_evidence`, `sector_practice` ou `product_inference`;
- território, cultura e sistema de manejo aplicáveis;
- valor, unidade, intervalo e origem;
- dados mínimos e política para valores ausentes;
- justificativa e link de fonte;
- papel autorizado a aceitar exceção;
- efeito sobre confiança e status de liberação.

### 15.2 Regras iniciais

| ID | Condição | Resultado | Natureza |
|---|---|---|---|
| `BASIN_001` | Área contribuinte termina na borda do talhão | Bloquear PCX | Inferência de segurança apoiada no princípio de microbacia |
| `SOIL_001` | Perfil, profundidade ou permeabilidade ausentes | Bloquear TI/TD/ESD executivo | Inferência de produto apoiada no IAC |
| `TI_001` | Grupo equivalente ao IAC 3 ou 4 | Não recomendar TI no pack paulista | Fonte oficial regional |
| `TI_002` | Volume útil menor que volume de projeto | Reprovar seção | Fonte oficial/metodologia hidráulica |
| `TI_003` | Esvaziamento incompatível com eventos sucessivos | Reprovar ou exigir TD/híbrido | Inferência de segurança apoiada por evidência primária |
| `TD_001` | Não existe receptor seguro | Bloquear TD | Fonte oficial e inferência de segurança |
| `TD_SP_002` | TD no pack IAC/SP sem CEV/prado previamente estabilizado | Bloquear implantação e conexão | Fonte oficial regional |
| `CEV_001` | CEV não estabilizado | Bloquear conexão a montante | Fonte oficial regional |
| `CEV_002` | Talvegue, curso d'água ou APP rotulado automaticamente como CEV | Bloquear receptor até análise hidráulica, ambiental e legal | Inferência de segurança e conformidade |
| `PASS_001` | Estrutura passante | Exigir seção degradada e monitoramento | Fonte oficial regional |
| `EMB_001` | Canal embutido expõe subsolo | Gerar recomendação de correção e custo | Fonte oficial regional |
| `FURROW_001` | Linha cria concentração ou saída desprotegida | Bloquear linha | Fonte oficial e inferência computacional |
| `ESD_001` | Cenário ESD sem PCE, PCX e caminho de excesso | Bloquear cenário | Fonte oficial/inferência de modelagem |
| `ESD_ROLE_001` | Linha importada sem função geométrica e hidráulica confirmadas | Excluir do cálculo e bloquear locação | Inferência de modelagem |
| `ESD_END_001` | Alcance termina sem receptor estável e verificado | Bloquear alcance | Fonte oficial/inferência de segurança |
| `ESD_CONC_001` | Descarga converge de forma não dimensionada em poucas linhas | Reprovar família e gerar alternativa | Fonte primária/inferência computacional |
| `ESD_ROAD_001` | Carreador recebe ou transfere vazão sem drenagem projetada | Bloquear cenário | Fonte oficial regional/inferência de segurança |
| `ST_001` | ST fora das condições do pack paulista | Tratar como inovação e exigir revisão especial | Fonte oficial regional |
| `ST_SP_002` | ST no pack IAC/SP sem CEV/prado previamente estabilizado | Bloquear liberação | Fonte oficial regional |
| `RUSLE_001` | RUSLE usada como prova de capacidade da enxurrada | Bloquear PCX; exigir hidrologia e hidráulica | Limitação do método e inferência de segurança |
| `HARV_001` | Raio de trabalho ausente ou substituído pelo raio de manobra | Bloquear colheitabilidade | Inferência de produto apoiada em cinemática |
| `HARV_002` | Envelope articulado/offtracking não validado | Bloquear rota, portal e manobra | Evidência primária/inferência de segurança |
| `SOIL_TRAFFIC_001` | Tensão aplicada excede capacidade no teor de água simulado | Bloquear tráfego e POA no caso | Evidência primária/inferência de segurança |
| `LEGAL_001` | Descarga ou obra afeta APP, curso d’água ou terceiro | Exigir análise legal e ambiental | Inferência de conformidade |
| `ASBUILT_001` | Cota, seção ou saída executada fora da tolerância aprovada | Não liberar operação | Inferência de segurança |

### 15.3 Valores de referência não universais

Os seguintes valores devem entrar como parâmetros de fonte, nunca como constantes globais:

| Valor | Contexto | Tratamento no produto |
|---|---|---|
| Base larga até cerca de 6% | Orientação da Embrapa para cana | Advertência regional/técnica, não aprovação |
| Sulcação em nível entre 3% e 12%, chegando a 18% em solo resistente | IAC, cana em São Paulo | Condicional a solo, manejo e PCX |
| Embutido invertido entre 10% e 18% | IAC, recomendação paulista | Cenário sujeito a risco e validação |
| TD com 3/1000 comum e até 7/1000 | Metodologia IAC | Recalcular por vazão, seção e velocidade |
| Velocidade de 0,60 a 0,75 m/s | Tabelas/metodologia IAC para TD | Referência, não velocidade permissível universal |
| Período de retorno de 10 anos | Referência IAC | Mínimo configurável conforme risco e critério vigente |
| Linha ESD a 2% em vertente de 10% | Exemplo fotográfico da ANA | `example_source`; proibido usar como default |

## 16. Dados mínimos e bloqueadores

### 16.1 Obrigatórios para anteprojeto confiável

- limite dos talhões e contexto da propriedade;
- MDT ou nuvem classificada com metadados e controle de qualidade;
- rede de drenagem, estradas e estruturas existentes;
- cobertura do solo e histórico de erosão;
- solo inferido com fonte e incerteza explícita;
- chuva de referência regional;
- frota e padrão de sulcação.

### 16.2 Obrigatórios para projeto liberável

- topografia validada por pontos independentes;
- microbacia completa a montante e destino a jusante;
- perfil de solo e infiltração representativos;
- IDF local e período de retorno aceito;
- levantamento de terraços, bueiros, canais, nascentes e áreas úmidas;
- seção e estabilidade do receptor;
- plano de implantação, manutenção e emergência;
- vistoria e aprovação do responsável técnico.

### 16.3 Bloqueadores absolutos

1. Sistema de referência, unidade vertical ou qualidade altimétrica desconhecidos.
2. Bacia truncada pelo arquivo do cliente.
3. TI sem caracterização hidráulica do perfil.
4. TD, CEV ou ESD sem saída segura.
5. Contribuição de estrada ou vizinho ignorada.
6. Estrutura de descarga ainda não estabilizada.
7. Interferência ambiental ou com terceiros sem avaliação.
8. Geometria executiva sem profissional habilitado e controle as built.
9. APP, curso d'água ou talvegue usado como receptor por classificação automática.
10. Tiro, rota, portal, cabeceira ou POA que falha no envelope articulado ou na capacidade de suporte do solo úmido.

A [Lei paulista 6.171/1988](https://www.al.sp.gov.br/repositorio/legislacao/lei/1988/compilacao-lei-6171-04.07.1988.html) determina planejamento segundo a capacidade de uso e estabelece que o uso adequado do solo deve ser planejado independentemente das divisas da propriedade. Mesmo fora de São Paulo, o princípio físico permanece: o talhão administrativo não limita a água.

## 17. Riscos e modos de falha

| Falha | Causa frequente | Detecção/controle |
|---|---|---|
| Rompimento em cascata | Crista baixa, seção insuficiente, contribuição externa | Perfil longitudinal, volume/vazão e inspeção após chuva |
| TI permanece cheio | Perfil lento, compactação, selamento, lençol | Ensaio, tempo de esvaziamento e monitoramento |
| Erosão em sulcos | Gradiente ou tiro excessivo, confluência | Perfil de cada linha e mapa de contribuição |
| Solapamento do invertido | Extravasamento e turbulência no talude | Cenário de falha, bordo livre e proteção |
| Passante perde capacidade | Tráfego e rebaixamento | Seção as built e seção degradada simulada |
| CEV vira voçoroca | Conexão antes da estabilização ou velocidade excessiva | Bloqueio de conexão, vegetação e dissipação |
| Assoreamento | Sedimento alto, baixa velocidade, manutenção insuficiente | Balanço de sedimentos e inspeção |
| Água de estrada sobrecarrega sistema | Bueiro/carreador fora do modelo | Inventário e microbacia integrada |
| ESD concentra em caminho preferencial | Rugosidade, linhas ou carreadores mal orientados | Simulação pós-projeto e vistoria de eventos |
| Projeto não executado como calculado | Erro de locação, máquina ou cota | GNSS, tolerâncias e levantamento as built |

O sistema deve simular também a consequência de falha, não somente a condição nominal. O rompimento de uma estrutura pode concentrar a água e destruir estruturas a jusante.

## 18. O que é automatizável

O TerraFlux pode automatizar:

- controle de qualidade de MDT e nuvem;
- condicionamento hidrológico, microbacias e caminhos de fluxo;
- mapas de declividade, comprimento de rampa e convergência;
- geração preliminar de TI, TD, base larga, embutido, híbrido, ESD e ST;
- espaçamento vertical e horizontal conforme pack escolhido;
- volume de TI, vazão de TD/CEV, Manning e verificação de seções;
- perfis longitudinais, gradientes, pontos baixos e extravasamentos;
- análise de sulcos, carreadores, cabeceiras e operação das máquinas;
- cenários de cobertura, compactação, chuva e manutenção;
- área útil, movimento de terra, custos e indicadores operacionais;
- memorial de cálculo rastreável e comparação de alternativas;
- controle de versões e confronto projeto versus as built.

Resultados automáticos devem permanecer em estado `preliminar` enquanto dados ou aprovações obrigatórias estiverem pendentes.

## 19. O que exige decisão profissional

Exige profissional legalmente habilitado, com atribuição compatível:

- validar levantamento, solos e representatividade dos ensaios;
- selecionar método, chuva, período de retorno, coeficientes e velocidades admissíveis;
- aceitar ou rejeitar TI, TD, ESD, ST e soluções híbridas;
- aprovar receptor, CEV, dissipação e consequência a jusante;
- compatibilizar conservação, manejo agronômico e mecanização;
- definir tolerâncias construtivas e sequência de implantação;
- vistoriar, revisar, emitir projeto, fiscalizar e aceitar o as built;
- avaliar legislação ambiental, estadual, municipal e direitos de terceiros;
- emitir ART quando o serviço contratado estiver sujeito a ela.

A [Resolução Confea 218/1973](https://normativos.confea.org.br/Ementas/Visualizar?id=266) inclui engenharia rural, irrigação e drenagem agrícola, edafologia, utilização do solo e mecanização entre os campos do engenheiro agrônomo, sempre limitados às atribuições efetivamente registradas. A [Lei 6.496/1977](https://normativos.confea.org.br/Ementas/Visualizar?id=28) sujeita contratos de serviços profissionais de Engenharia e Agronomia à ART e define nela o responsável legal.

O produto não deve fixar um único título profissional como suficiente para todos os casos. Estruturas hidráulicas, levantamentos e interferências ambientais podem exigir competências complementares. A regra correta é verificar a atribuição registrada no CREA e o escopo concreto do serviço.

## 20. Fluxo de aprovação recomendado

1. **Triagem:** dados públicos e levantamento do cliente; nenhuma geometria executiva.
2. **Diagnóstico:** microbacia, solo, água, erosão, estruturas e conflitos.
3. **Cenários:** TI, TD, passante, embutido, ESD/ST e híbridos comparados.
4. **Revisão técnica:** premissas e rule pack aceitos ou substituídos com justificativa.
5. **Vistoria:** solo, pontos de concentração, estradas, saídas e interferências conferidos.
6. **Projeto:** memoriais de PCE e PCX, seções, linhas, execução e manutenção.
7. **Responsabilidade:** aprovação e ART quando aplicável.
8. **Locação e execução:** arquivos controlados, marcos e tolerâncias.
9. **As built:** cotas, seções e saídas comparadas ao projeto.
10. **Monitoramento:** inspeção após chuvas, manutenção e realimentação do modelo.

Nenhuma mensagem da interface deve afirmar que “a IA aprovou” uma obra. O sistema calcula, compara, alerta e registra; o profissional habilitado decide e responde pelo projeto.

## 21. Referências principais

- Instituto Agronômico de Campinas. [Boletim Técnico IAC 216 - Recomendações gerais para a conservação do solo na cultura da cana-de-açúcar](https://www.iac.sp.gov.br/media/publicacoes/iacbt126.pdf).
- Agência Nacional de Águas e Saneamento Básico. [Manual do Programa Produtor de Água, volume 5 - Práticas Mecânicas de Conservação de Solo e Recursos Hídricos](https://www.gov.br/ana/pt-br/acesso-a-informacao/acoes-e-programas/programa-produtor-de-agua/manuais-e-capacitacao/manuais/manual-programa-produtor-de-agua-volume-5).
- Embrapa. [Manejo e conservação na cultura da cana-de-açúcar](https://www.embrapa.br/en/web/agencia-de-informacao-tecnologica/cultivos/cana-de-acucar/producao/correcao-e-adubacao/manejo-e-conservacao).
- Embrapa Cerrados. [Recomendações técnicas para dimensionamento e construção de terraços](https://www.embrapa.br/en/busca-de-solucoes-tecnologicas/-/produto-servico/11068/recomendacoes-tecnicas-para-dimensionamento-e-construcao-de-terracos).
- Franco, Alexandre Puglisi Barbosa. USP/ESALQ. [Percepção, recomendação e adoção do terraceamento agrícola comparadas ao seu funcionamento](https://teses.usp.br/teses/disponiveis/11/11140/tde-21012019-150102/pt-br.html).
- Agraer/Semadesc. [Capacitação sobre Escoamento Superficial Difuso para conservação de solo e água](https://www.agraer.ms.gov.br/tecnicos-da-agraer-participam-de-capacitacao-sobre-tecnica-de-escoamento-superficial-difuso-para-conservacao-de-solo-e-agua/).
- Assembleia Legislativa do Estado de São Paulo. [Lei 6.171/1988 - uso, conservação e preservação do solo agrícola](https://www.al.sp.gov.br/repositorio/legislacao/lei/1988/compilacao-lei-6171-04.07.1988.html).
- Confea. [Resolução 218/1973 - atividades e atribuições profissionais](https://normativos.confea.org.br/Ementas/Visualizar?id=266).
- Confea. [Lei 6.496/1977 - Anotação de Responsabilidade Técnica](https://normativos.confea.org.br/Ementas/Visualizar?id=28).
