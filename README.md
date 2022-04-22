# cameron_pdf_tools
Some very basic pdf tools - built in literally a couple of hours.

The metadata extractor works - slowly. But it should point you in the right direction.

For generating file names, you can extract the documents metadata - if it's set - using the metadata_extractor script here. These can then be used to make titles.

If it's not properly set, tricks with pdfminer can be done to guess the title by itterarting over text fields - assuming the documents are consistent.

Suggest using pdftohtml to generate searchable text for the entire documents. If the documents have embedded text.

Or either
gscan2pdf (faster)
or 
ocrmypdf (python, and so modifiable. Also. Better) if the PDFs turn out not to have embedded text.

To install

On windows - use WSL for favourite. Clone the repo, cd into it. Then run

`python -m pip install -r requirements.txt`

Then

`python setup.py develop`

Should allow you to play around with the metadata_extractor script. Also gets you ocrmypdf - which is just handy.





