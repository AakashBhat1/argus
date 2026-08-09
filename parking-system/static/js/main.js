// Enhanced JavaScript with better interactivity
document.addEventListener('DOMContentLoaded', function() {
    console.log('Smart Parking System initialized');
    
    // Add preload class to body to prevent transition flashing
    document.body.classList.add('preload');
    
    // Remove preload class after page has loaded
    setTimeout(function() {
        document.body.classList.remove('preload');
    }, 500);
    
    // Fix for image loading causing layout shifts
    const featureImages = document.querySelectorAll('.feature img');
    featureImages.forEach(img => {
        // Set fixed dimensions before image loads
        img.style.minHeight = '100px';
        img.style.minWidth = '100px';
        
        // When image loads, remove fixed dimensions if needed
        img.addEventListener('load', function() {
            // Keep dimensions to prevent layout shift
        });
    });
    
    // Prevent scrolling issues by debouncing scroll events
    let scrollTimeout;
    window.addEventListener('scroll', function() {
        clearTimeout(scrollTimeout);
        document.body.classList.add('is-scrolling');
        
        scrollTimeout = setTimeout(function() {
            document.body.classList.remove('is-scrolling');
        }, 100);
    });
    
    // Add animations for page elements
    function animateElements() {
        const elements = document.querySelectorAll('.feature, .card, .btn');
        elements.forEach((element, index) => {
            setTimeout(() => {
                element.classList.add('animated');
            }, 100 * index);
        });
    }
    
    // Run animations after a short delay
    setTimeout(animateElements, 200);
    
    // Add hover effects for interactive elements
    const interactiveElements = document.querySelectorAll('.feature, .btn, .card');
    interactiveElements.forEach(element => {
        element.addEventListener('mouseenter', function() {
            this.style.transform = 'translateY(-5px)';
            this.style.boxShadow = '0 8px 20px rgba(0, 0, 0, 0.25), 0 0 15px rgba(0, 123, 255, 0.3)';
        });
        
        element.addEventListener('mouseleave', function() {
            this.style.transform = '';
            this.style.boxShadow = '';
        });
    });
});

// Fix height of feature blocks to be equal
function equalizeFeatureHeights() {
    const features = document.querySelectorAll('.feature');
    if (features.length < 2) return;
    
    // Reset heights
    features.forEach(feature => {
        feature.style.height = 'auto';
    });
    
    // Skip on mobile
    if (window.innerWidth < 768) return;
    
    // Find tallest
    let maxHeight = 0;
    features.forEach(feature => {
        const height = feature.offsetHeight;
        maxHeight = Math.max(maxHeight, height);
    });
    
    // Apply height to all
    if (maxHeight > 0) {
        features.forEach(feature => {
            feature.style.height = maxHeight + 'px';
        });
    }
}

// Run on page load and resize
window.addEventListener('load', equalizeFeatureHeights);
window.addEventListener('resize', equalizeFeatureHeights);
