<link rel="stylesheet" href="{{asset('css/receipt.css')}}">
<link rel="stylesheet" href="https://unpkg.com/purecss@2.0.3/build/pure-min.css"
      integrity="sha384-cg6SkqEOCV1NbJoCu11+bm0NvBRc8IYLRGXkmNrqUBfTjmMYwNKPWBTIKyw9mHNJ" crossorigin="anonymous">
<div id="invoice-POS">
    <center id="top">
        <div >
            <img src="{{ asset('img/logo/logo.gif') }}" width="100" height="100">
        </div>
        <div class="info">
            <h2>{{auth()->user()->vendor->business_name }}</h2>
        </div><!--End Info-->
    </center><!--End InvoiceTop-->

    <div id="mid">
        <div class="info">
            <p>
                Address :{{ auth()->user()->vendor->address }}</br>
                Phone : {{ auth()->user()->vendor->phone }}</br>
                TIN : {{ auth()->user()->vendor->tin }}</br>
                VRN : {{ auth()->user()->vendor->vrn }}</br>
            </p>


            <div class="pure-g">
                <div class="pure-u-1-2"><p>Receipt No: {{numPat($payment->id,7,abbrvWords(auth()->user()->vendor->business_name))}}</p></div>
                <div class="pure-u-1-2"><p style="text-align: right">
                        Date: {{$payment->payment_date->format('d-m-Y H:i:s')}}</p></div>
            </div>

            <div style="text-align: center">
                <h5>To: {{$payment->bill->customer->name}}</h5>
            </div>

        </div>
    </div><!--End Invoice Mid-->

    <table class="pure-table" border="1" style="border: dotted 1px #a5a2a2">
        <caption>Paid in: {{$payment->provider->name}}</caption>
    </table>

    <table class="pure-table" border="1" style="border: dotted 1px #a5a2a2">
        <th>Particular</th>
        <th>Amount</th>
        <tr>
            <td style="text-align: center">{{$payment->bill->description}} for bill#{{ $payment->bill->id }}</td>
            <td style="text-align: center">{{number_format($payment->amount, 2)}}</td>
        </tr>
    </table>

    <div id="bot">
        <div id="legalcopy">
            <p class="legal"><strong>Thank you for choosing us!</strong> 
            </p>
        </div>

        <div>
            <p id="time_printed"></p>
            <p>Printed by :{{auth()->user()->username}}</p>
        </div>

    </div><!--End InvoiceBot-->
</div><!--End Invoice-->

<script>
    var today = new Date();
    var dd = today.getDate();
    var mm = today.getMonth() + 1; //January is 0!
    var yyyy = today.getFullYear();
    var h = today.getHours();
    var m = today.getMinutes();
    var s = today.getSeconds();
    if (h < 10) {
        h = '0' + h;
    }
    if (h < 10) {
        m = '0' + m;
    }
    if (h < 10) {
        s = '0' + s;
    }
    var time = h + ':' + m + ':' + s;

    if (dd < 10) {
        dd = '0' + dd
    }

    if (mm < 10) {
        mm = '0' + mm
    }
    today = dd + '/' + mm + '/' + yyyy + ' at ' + time;

    document.getElementById('time_printed').innerHTML = 'Printed on: ' + today
    window.print();
</script>
