# printtwin/certs

`bambu.pem` holds Bambu Lab's public certificate authorities (BBL CA, BBL CA2 RSA / ECC and the
device CAs of newer models). PrintTwin uses them to check that a Bambu printer's TLS certificate
was issued by Bambu - nothing here is secret, and no keys are included.

The certificates are copied unchanged from ha-bambulab
(https://github.com/greghesp/ha-bambulab, `custom_components/bambu_lab/pybambu/certs`), which is
distributed under the MIT licence:

```
MIT License

Copyright (c) 2023 ha-bambulab contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

A newer Bambu model whose certificate chain is not covered yet still connects: PrintTwin then
remembers the printer's certificate on first contact instead (see README, *Bambu Lab printers*).
