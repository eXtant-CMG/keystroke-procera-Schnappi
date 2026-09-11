# Keystroke Procera — Static Web App

A browser-based converter that turns Schnappi[^1] keystroke session data (.ses) into TEI-XML encoding. No server needed — all processing happens locally in the user's browser via [Pyodide](https://pyodide.org) (Python compiled to WebAssembly).

Please note that the TEI-XML encoding can contain various errors, e.g. the nesting might not be correct, the locations of the deletions and additions can be wrong, just as the indication of the type of the writing action ('new text', 'pre-contextual', 'contextual' or 'continue'). Please check the generated output carefully and correct errors accordingly. Once the TEI-XML file is corrected, it can be visualised using [Keystroke Loxensis Light](https://github.com/eXtant-CMG/Keystroke-Loxensis-Light).

Made by [Lamyk Bekius](https://www.uantwerpen.be/en/staff/lamyk-bekius/) as a contribution to [CLARIAH-VL+](https://clariahvl.hypotheses.org/).

[^1]: Butzek, Anne-Marie (2023). Les processus de l’écriture littéraire du point de vue génétique et psycholinguistique : étude de cas. Thèse en Lettres, université d’Aix-Marseille.