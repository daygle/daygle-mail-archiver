(function(){
    if (window.createToast) return; // idempotent

    // process queued toasts created before this script loaded
    function processQueue(createFn){
        try{
            var q = window._toastQueue || [];
            for(var i=0;i<q.length;i++){
                try{ createFn(q[i].message, q[i].type); }catch(e){}
            }
            window._toastQueue = [];
        }catch(e){}
    }

    function _createToast(message, type){
        try{
            type = (type || 'info').toLowerCase();
            var typeClass = type === 'success' ? 'text-bg-success' : (type === 'error' ? 'text-bg-danger' : (type === 'warning' ? 'text-bg-warning' : 'text-bg-info'));

            var container = document.getElementById('toastContainer');
            if(!container){
                container = document.createElement('div');
                container.id = 'toastContainer';
                container.style.position = 'fixed';
                container.style.right = '16px';
                container.style.bottom = '16px';
                container.style.zIndex = 12000;
                document.body.appendChild(container);
            }

            var toastEl = document.createElement('div');
            toastEl.className = 'toast align-items-center ' + typeClass + ' border-0';
            toastEl.setAttribute('role','alert');
            toastEl.setAttribute('aria-live','assertive');
            toastEl.setAttribute('aria-atomic','true');
            toastEl.style.minWidth = '180px';
            toastEl.innerHTML = '\n                <div class="d-flex">\n                    <div class="toast-body">' + (message || '') + '</div>\n                    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>\n                </div>\n            ';

            container.appendChild(toastEl);

            // If bootstrap.Toast is available, use it; otherwise fallback to auto-hide removal
            if (window.bootstrap && bootstrap.Toast) {
                var bt = new bootstrap.Toast(toastEl, { delay: 2500 });
                bt.show();
                toastEl.addEventListener('hidden.bs.toast', function(){ toastEl.remove(); });
            } else {
                // fallback: remove after timeout
                setTimeout(function(){ try{ toastEl.remove(); }catch(e){} }, 2500);
            }
        }catch(e){ console.log('createToast fallback:', message); }
    }

    // expose
    window.createToast = _createToast;
    window.showToast = function(message, type){ return window.createToast(message, type); };

    // If there were queued messages, process them now
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        processQueue(_createToast);
    } else {
        window.addEventListener('DOMContentLoaded', function(){ processQueue(_createToast); });
    }

    // Show any initial flash provided by server
    try{
        function displayInitialFlash(){
            if (window.__initialFlash) {
                try{ _createToast(window.__initialFlash.message || window.__initialFlash, window.__initialFlash.type || 'info'); }catch(e){}
                window.__initialFlash = null;
            }
        }

        if (window.bootstrap && bootstrap.Toast) {
            displayInitialFlash();
        } else {
            window.addEventListener('load', displayInitialFlash);
        }
    }catch(e){}

})();
